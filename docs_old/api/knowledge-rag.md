# Knowledge 및 RAG API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: feature/mba-89 @ 3a1d6799118f5a6bb50414914b40865e6375e35f (base dev @ 5e67adba265346009fbbc691ee16e287cd89548e, PR #142 follow-up, 2026-07-01 KST)
Related ADRs: [ADR-202606271559-audit-log-rag-trace-storage](../decisions/ADR-202606271559-audit-log-rag-trace-storage.md), [ADR-202606290124-mvp2-classification-metadata-storage](../decisions/ADR-202606290124-mvp2-classification-metadata-storage.md), [ADR-202606290131-audit-action-naming-standard](../decisions/ADR-202606290131-audit-action-naming-standard.md), [ADR-202606301045-metadata-aware-hierarchical-rag-boundary](../decisions/ADR-202606301045-metadata-aware-hierarchical-rag-boundary.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 범위

Knowledge base, document, chunk preview, RAG search test, ingestion 계약을 정의한다.

## Knowledge Base 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/knowledge` | `KnowledgeBaseCreate` | `KnowledgeBaseResponse` | authenticated; primary organization fallback |
| Implemented | `GET` | `/api/v1/knowledge` | 없음 | `KnowledgeBaseResponse[]` | current user owner scope |
| Implemented | `GET` | `/api/v1/knowledge/{kb_id}` | 없음 | `KnowledgeBaseDetailResponse` | current user owner scope |
| Implemented | `PATCH` | `/api/v1/knowledge/{kb_id}` | `KnowledgeUpdate` | `204` | current user owner scope |
| Implemented | `DELETE` | `/api/v1/knowledge/{kb_id}` | 없음 | `204` | current user owner scope |
| Implemented | `GET` | `/api/v1/knowledge/{kb_id}/documents/{document_id}` | 없음 | `DocumentResponse` | current user owner scope |
| Implemented | `GET` | `/api/v1/knowledge/{kb_id}/documents/{document_id}/content` | 없음 | file/html/redirect | current user owner scope |
| Implemented | `POST` | `/api/v1/knowledge/{kb_id}/documents/{document_id}/process` | `DocumentPreviewRequest` | `202` processing status | current user owner scope |
| Implemented | `POST` | `/api/v1/knowledge/{kb_id}/documents/{document_id}/preview` | `DocumentPreviewRequest` | `DocumentPreviewResponse` | current user owner scope |
| Implemented | `POST` | `/api/v1/knowledge/{kb_id}/documents/{document_id}/sync` | 없음 | `202` sync status | current user owner scope |

## RAG 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/rag/upload/presigned-url` | file metadata | presigned URL | authenticated |
| Implemented | `POST` | `/api/v1/rag/upload` | multipart/form-data | `IngestionResponse` | authenticated; 신규 KB는 current user owner, 기존 `knowledgeBaseId`는 현재 owner 검증 없음 |
| Implemented | `POST` | `/api/v1/rag/document/{document_id}/analyze` | 없음 | `DocumentAnalyzeResponse` | authenticated; 현재 owner/KB scope 검증 없음 |
| Implemented | `POST` | `/api/v1/rag/document/{document_id}/confirm` | confirm payload | result | authenticated; 현재 owner/KB scope 검증 없음 |
| Implemented | `DELETE` | `/api/v1/rag/document/{document_id}` | 없음 | result | current user owner scope |
| Implemented | `POST` | `/api/v1/rag/search-test/chat` | `SearchQuery` | `RAGResponse` | `X-Organization-Id` 필수; KB `use` 권한 필요 |
| Implemented | `POST` | `/api/v1/rag/search-test/pure` | `SearchQuery` | `ChunkPreview[]` | `X-Organization-Id` 필수; KB `use` 권한 필요 |
| Implemented | `GET` | `/api/v1/rag/document/{document_id}/progress` | 없음 | `text/event-stream` | authenticated; event generator는 현재 document id만 조회 |
| Implemented | `POST` | `/api/v1/rag/proxy/preview` | `ApiPreviewRequest` | proxy result | authenticated |
| Implemented | `POST` | `/api/v1/rag/agent/answer` | `RAGAgentAnswerRequest` | `RAGAgentAnswerResponse` | `X-Organization-Id` 필수; KB `use` 권한과 LLM credential 사용 권한 필요 |
| Implemented | `POST` | `/api/v1/rag/agent/answer/stream` | `RAGAgentAnswerRequest` | `text/event-stream` | `X-Organization-Id` 필수; KB `use` 권한과 LLM credential 사용 권한 필요 |

## RAG Agent Answer 계약

RAG Agent answer endpoint는 사용자가 직접 질문하면 서버가 metadata-aware/hierarchical retrieval을 수행하고, 검색된 citation summary를 근거로 LLM 답변을 생성하는 UI-facing API다. 이 endpoint는 workflow 실행이 아닐 수 있으므로 workflow trace endpoint와 같은 실행 모델로 보지 않는다.

RAG 확장 3단계의 목표 request는 단일 `knowledge_base_id`만 지원한다. 여러 KB를 동시에 검색하는 multi-KB Agent answer는 후속 API 계약으로 분리한다.

목표 request 기준:

- `knowledge_base_id`
- `query`
- `metadata_filter`
- `classification_filter`
- `tags`
- `hierarchy_mode`
- `top_k`
- `generation_model_id` required. 3단계에서는 generation model을 요청에서 명시해야 하며, 이름순/생성일순 같은 fallback으로 model을 고르지 않는다.
- `credential_id` required. 3단계 기본 계약은 credential 자동 선택을 하지 않는다. Default credential 또는 preset 기반 자동 선택은 별도 data model/API 계약으로 공식화한 뒤 후속 확장으로 다룬다.
- `correlation_id` optional. 없으면 서버가 생성한다.

Streaming 여부는 endpoint로 결정한다. `/api/v1/rag/agent/answer`는 일반 JSON response를 반환하고, `/api/v1/rag/agent/answer/stream`은 SSE stream을 반환한다. Request body의 `stream` flag는 목표 계약에 포함하지 않는다.

`correlation_id`는 추적용 opaque id이며 권한 판정, resource 조회, tenant scope 판정에 사용하지 않는다. Client가 전달할 수 있지만 secret, API key, prompt 원문, email, token, credential value를 넣으면 안 된다. 서버는 길이와 문자셋을 제한해야 한다. 권장 형식은 255자 이하의 UUID/ULID 또는 `[A-Za-z0-9._:-]` 범위 문자열이다. 값이 없으면 서버가 생성하고, client가 제공한 값이 유효하지 않으면 `400 invalid_correlation_id`로 거부한다. 같은 `correlation_id`를 여러 answer run이 공유할 수 있으므로, 서버는 이 값을 resource lookup key나 uniqueness guarantee로 사용하지 않는다.

3단계 초기 목표 guardrail은 다음과 같다. `top_k`는 기본값 8, 최대 8로 제한한다. Client가 최대값보다 큰 `top_k`를 보내면 `400 validation.failed`로 거부한다. Context token budget 8000, max output tokens 1000, citation preview 300 characters, provider timeout 60 seconds, retrieval timeout 30 seconds는 서버 내부 cap이며 3단계 public request field로 열지 않는다. Context token budget 8000과 max output tokens 1000은 상한이며, 선택한 generation model의 `context_window`에서 output reserve와 system prompt overhead를 뺀 값이 더 작으면 서버는 context budget과 provider `max_tokens`를 더 작은 model-aware 값으로 낮춰야 한다. Stream 시작 전 provider/preflight 실패는 HTTP status와 reason code로 반환할 수 있지만, SSE stream이 시작된 뒤에는 HTTP status를 바꿀 수 없으므로 terminal `error` event의 `reason_code`로 의미를 전달한다. Retrieval 단계가 timeout cap을 초과하면 stream 이후 `reason_code="stream.timeout"` terminal event로 닫고, non-stream 응답에서는 sanitized `generation.failed`로 닫는다. Provider 호출이 provider timeout cap을 초과하면 `reason_code="provider.timeout"` terminal event 또는 HTTP `504 provider.timeout`으로 닫는다. Provider facade token streaming과 heartbeat가 공식화되기 전까지 generation 구간의 token heartbeat/idle timeout은 적용하지 않는다. 제품/UX 검증 후 cap을 바꿔야 하면 구현 전에 API 문서와 운영 설정 문서를 함께 갱신한다.

Request schema validation, invalid `correlation_id`, invalid organization header, missing required header/body field처럼 answer 실행을 시작하기 전 판정 가능한 오류는 `rag_answer_runs` row를 만들지 않고 `rag.answer.*` lifecycle audit도 남기지 않는다. KB 없음, inactive KB, scope 밖 KB, organization mismatch처럼 `404 resource.not_found`로 숨겨야 하는 경우도 resource hiding을 유지하기 위해 answer run을 만들지 않는다. Schema validation, organization header validation, active organization scope 확인, KB scope visibility 확인, required credential/model visibility 확인을 모두 통과한 뒤 answer run을 생성하고 `rag.answer.requested`를 기록한다.

목표 response 기준:

- `answer_run_id`
- `correlation_id`
- `status`
- `answer`
- `citations`
- `retrieval_summary`
- `usage_summary`
- `policy_result`

Model/credential resolution은 다음 순서를 따른다.

- `generation_model_id`와 `credential_id`는 3단계 request에서 모두 필수다. 누락되면 schema validation 실패이며 answer run을 만들지 않는다.
- 기본 UI는 `/api/v1/llm/agent-answer-options`가 반환하는 verified model/credential pair 중 하나를 선택해야 한다. 모델과 credential을 별도 목록에서 독립 선택해 relation 검증을 클라이언트가 추정하지 않는다.
- `generation_model_id`가 가리키는 model이 존재하지 않거나 inactive이거나 숨겨야 하는 scope 밖 resource이면 answer run 생성 없이 `404 resource.not_found`로 닫는다.
- `credential_id`가 가리키는 credential의 존재, active 상태, credential organization과 `X-Organization-Id` 일치를 answer run 생성 전에 확인한다.
- 3단계 기본 계약의 `credential_id`는 answer generation credential이다. Retrieval query embedding은 KB의 `embedding_model`을 사용하므로, 서버는 answer run 생성 후 retrieval 시작 전에 해당 embedding model을 지원하는 same-organization valid credential, verified credential-model relation, credential `use` 권한이 있는지 preflight해야 한다. 이 preflight는 router/LLM이 임의 credential을 고르는 동작이 아니라 KB embedding readiness 검증이다.
- 3단계 기본 계약은 generation credential 자동 선택을 하지 않는다. 이름순/생성일순 같은 우발적 fallback으로 generation credential을 고르지 않는다.
- Server-side RAG preset 또는 default credential 정책은 data model과 request field가 별도 공식화된 뒤 추가할 수 있다. 해당 후속 확장에서 deterministic하게 credential/model을 선택할 수 없을 때만 `409 credential_selection_required`를 사용한다.
- Preset이 credential/model을 지정하더라도 최종 model/credential에 동일한 scope, `use` 권한, verified relation 검증을 적용한다.
- 다른 organization credential로 fallback하지 않는다.
- 검증 실패는 같은 organization scope 안 action 권한 또는 verified relation 부족이면 answer run 생성 뒤 `403 permission.denied`와 `status="blocked"`, scope 밖 또는 mismatch resource이면 answer run 생성 없이 `404 resource.not_found` 정책을 따른다.

`answer_run_id`는 RAG 도메인의 `rag_answer_runs.id`다. Trace/usage table에 `rag_answer_run_id` FK를 추가하지 않는다. Workflow runtime에서 발생한 RAG retrieval evidence는 계속 `trace_payloads.payload_kind='rag.retrieval'`로 저장하지만, standalone Agent answer의 retrieval/citation evidence는 `rag_answer_runs`의 redaction-safe summary와 citation summary로 저장한다. Trace/usage/audit와의 느슨한 연결은 `correlation_id`로 한다.

`answer`는 API/stream response로 반환되는 final answer다. 기본 durable storage는 raw final answer나 provider raw completion을 저장하지 않고, `rag_answer_runs`의 redaction-safe answer summary, nullable top-level `answer_hash`, retrieval summary, citation summary, policy result, usage snapshot에 한정한다. 나중에 answer history/replay가 제품 요구사항이 되면 raw provider response가 아니라 별도 redacted answer snapshot과 retention/access policy를 공식 문서와 ADR로 먼저 확정한다.

`retrieval_summary` durable field allowlist는 `knowledge_base_id`, `hierarchy_mode`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, `score_summary`, `latency_ms`, `raw_content_returned`로 제한한다. `raw_content_returned`는 기본 `false`여야 한다.

`citation_summary` durable field allowlist는 citation별 `citation_id`, `document_id`, `chunk_id`, `rank`, `score`, `filename`, `heading`, `hierarchy_path`, `metadata_summary`로 제한한다. `metadata_summary`에는 classification, tags, source_type, effective range 같은 safe metadata만 포함하고 chunk content를 포함하지 않는다. User-facing JSON/SSE response의 `citations[].content_preview`는 Agent answer response에서 허용되는 유일한 content-derived citation field이며, redaction을 거친 뒤 최대 300자로 제한한다. Durable `citation_summary`, audit metadata, trace metadata, usage metadata에는 `content_preview`나 raw chunk content를 저장하지 않는다.

`query_hash`, `answer_hash`, `hash_version`은 nullable이다. 값을 저장하려면 HMAC-SHA256, server-side secret/pepper, `hash_version`을 함께 사용하는 정책을 먼저 구현해야 한다. HMAC secret/pepper가 설정되지 않았으면 값을 `null`로 두며, 일반 SHA-256 같은 unsalted hash fallback은 허용하지 않는다. `answer_hash`는 `rag_answer_runs.answer_hash` top-level column을 canonical 위치로 둔다. `answer_summary` durable field allowlist는 `answer_length`, `cited_document_count`, `citation_ids`, `policy_result`, `completion_status`, 선택적 `redacted_summary`로 제한한다. `answer_summary.answer_hash` mirror를 별도로 만들지 않는다. `redacted_summary`는 raw final answer 재구성이 가능할 정도로 긴 본문을 저장하지 않는다.

Durable/internal `usage_summary`는 answer 실행 시점에 캡처한 denormalized token/cost/latency snapshot이다. Canonical LLM usage 원천은 계속 `llm_usage_logs`이며, usage 도메인에 generic `correlation_id` 또는 metadata extension이 추가되기 전까지 `usage_summary`와 `llm_usage_logs`가 강한 FK 정합성을 가진다고 보지 않는다. Durable/internal 허용 field는 `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_cost`, `latency_ms`, `model_id`, `model_name`, `provider`, `credential_id` 같은 집계/식별자 값으로 제한한다. Credential 원문, API key, token, encrypted_config, raw prompt/completion, provider raw response는 `usage_summary`에 넣지 않는다.

User-facing response의 `usage_summary`는 durable/internal snapshot보다 좁은 whitelist를 사용한다. 일반 사용자 응답에는 `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_cost`, `latency_ms`, `model_name`, `provider`만 기본 포함하고, `credential_id`와 internal `model_id`는 privileged trace/audit response 또는 별도 admin API에서만 노출할 수 있다. `credential_id`는 secret이 아니지만 credential 원문 조회 권한을 의미하지 않으므로 일반 answer response의 기본 필드로 두지 않는다.

Streaming 응답은 최종 answer chunk와 함께 citation summary, retrieval summary, usage summary, `answer_run_id`, `correlation_id`를 반환해야 한다. SSE의 `citations[].content_preview`도 JSON response와 같은 redaction 및 300자 cap을 따른다. Raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response는 stream event나 저장 metadata에 기본 포함하지 않는다.

SSE event 계약은 다음 순서를 기본으로 한다. Stream 시작 전 검증 가능한 오류는 일반 HTTP status와 reason code로 반환한다. Stream이 시작된 뒤 오류가 발생하면 `error` event를 terminal event로 보내고 연결을 종료한다.

| Event | 순서 | Payload 기준 |
| --- | --- | --- |
| `retrieval.started` | 첫 event | `answer_run_id`, `correlation_id`, `status="running"`, `knowledge_base_id`, `hierarchy_mode` |
| `retrieval.completed` | retrieval 성공 후 | `answer_run_id`, `correlation_id`, `retrieval_summary`, `citations` 또는 `citation_summary` |
| `answer.delta` | 0회 이상 | `answer_run_id`, `correlation_id`, `delta`, `index` |
| `usage` | answer 종료 직전 | user-facing `usage_summary` whitelist |
| `summary` | answer 종료 직전 | `retrieval_summary`, `citation_summary`, `answer_summary`, `policy_result` |
| `answer.completed` | 성공 terminal event | `answer_run_id`, `correlation_id`, `status="completed"` |
| `error` | 실패 terminal event | `answer_run_id`, `correlation_id`, `status`, `reason_code`, `retryable` |

SSE event 이름은 audit action이 아니다. 예를 들어 SSE `answer.completed` event와 audit action `rag.answer.completed`는 이름이 비슷하지만 서로 다른 저장 위치와 의미를 갖는다. 3단계 구현은 run 생성과 permission preflight 후 stream을 열고 `retrieval.started`를 먼저 emit한다. Retrieval 단계에서 timeout cap을 넘으면 `stream.timeout` terminal error event로 닫고, provider 호출이 provider timeout cap을 넘으면 `provider.timeout` terminal error event로 닫는다. Provider facade에 token streaming interface가 아직 없으므로 `answer.delta`는 0회 또는 1회의 full-answer delta일 수 있으며, token 단위 progressive delta와 token heartbeat/idle timeout은 provider facade streaming 계약이 공식화된 뒤 확장한다.

RAG Agent answer lifecycle audit은 `rag.answer.requested`, `rag.answer.completed`, `rag.answer.failed`, `rag.answer.cancelled`를 사용한다. 성공 retrieval 감사 `rag.retrieve`, provider 호출 감사 `llm.call`, answer 실행 상태 `rag_answer_runs.status`와 의미를 섞지 않는다. `rag_answer_runs.status="blocked"`는 scope 안 resource가 확인된 뒤 policy 또는 permission 때문에 answer delta를 생성하지 못한 경우에만 사용한다. PII/classification/metadata policy 차단처럼 정책 판단 때문에 차단된 경우에는 HTTP `error.code="policy.blocked"`와 `policy.block` audit으로 표현한다. 이때 `policy.block` audit을 먼저 기록하고, 반환하는 403 예외에는 `audit_recorded=True` 또는 동등 marker를 설정해 Gateway 전역 401/403 handler가 `auth.permission_denied`를 중복 기록하지 않게 해야 한다. KB `use` 또는 LLM credential/model permission preflight 실패처럼 권한 판단 때문에 차단된 경우에는 HTTP `error.code="permission.denied"`와 `permission.denied` audit으로 표현한다. Resource hiding 대상인 `resource.not_found`, scope 밖, organization mismatch에는 answer run과 `blocked` status를 만들지 않는다. Invalid organization header와 validation 실패처럼 실행 전 검증에서 닫히는 오류도 answer run과 lifecycle audit을 만들지 않는다. 별도 `rag.answer.blocked` action은 만들지 않는다.

Non-streaming `/api/v1/rag/agent/answer`에서 answer run 생성 뒤 같은 scope 안 permission 또는 policy preflight가 차단되면 HTTP status는 `403`이고 목표 error envelope의 `error.details`에 `answer_run_id`, `correlation_id`, `status="blocked"`, `reason_code`를 포함한다. Permission block은 `error.code="permission.denied"`와 `reason_code="kb_use_denied"` 또는 `credential_use_denied` 계열을 사용하고, policy block은 `error.code="policy.blocked"`와 `reason_code="pii_policy_blocked"` 또는 `classification_policy_blocked` 계열을 사용한다.

```json
{
  "error": {
    "code": "permission.denied",
    "message": "요청한 작업을 수행할 권한이 없습니다.",
    "request_id": "req_xxx",
    "details": {
      "answer_run_id": "uuid",
      "correlation_id": "corr_xxx",
      "status": "blocked",
      "reason_code": "kb_use_denied"
    }
  }
}
```

이 details metadata는 운영 추적과 UI 상태 표시용이며 권한 판정이나 resource lookup key로 사용하지 않는다. Stream 시작 후 차단되면 terminal `error` event에 같은 safe field를 포함한다. Answer run을 만들지 않는 `resource.not_found`, scope 밖, organization mismatch, invalid header/validation 응답에는 `answer_run_id`를 포함하지 않는다.

RAG Agent answer 실패 단계별 처리는 다음 기준을 따른다.

| 단계 | HTTP / Stream 결과 | `rag_answer_runs` | Audit | 응답 metadata / 보안 기준 |
| --- | --- | --- | --- | --- |
| Request schema validation, missing required field, invalid organization header, invalid `correlation_id` | `400` 또는 `422` reason code | 미생성 | `rag.answer.*` lifecycle audit 없음 | `answer_run_id` 없음 |
| KB 없음, inactive KB, scope 밖 KB, organization mismatch, model/credential visibility mismatch | `404 resource.not_found` | 미생성 | `rag.answer.*` lifecycle audit 없음 | resource hiding 유지, `answer_run_id` 없음 |
| 같은 scope 안 KB `use` 권한 부족 | `403 permission.denied` | 생성 후 `status="blocked"` | `rag.answer.requested`, `permission.denied` | `reason_code="kb_use_denied"`, `audit_recorded=True` |
| 같은 scope 안 KB embedding credential `use` 권한 부족 | `403 permission.denied` | 생성 후 `status="blocked"` | `rag.answer.requested`, `permission.denied` | `reason_code="embedding_credential_use_denied"`, `audit_recorded=True` |
| 같은 scope 안 credential `use` 권한 부족 | `403 permission.denied` | 생성 후 `status="blocked"` | `rag.answer.requested`, `permission.denied` | `reason_code="credential_use_denied"`, `audit_recorded=True` |
| credential-model verified relation 없음 | `403 permission.denied` | 생성 후 `status="blocked"` | `rag.answer.requested`, `permission.denied` | `reason_code="credential_model_relation_denied"`, `audit_recorded=True` |
| KB embedding model/credential readiness 미충족, retrieval/generation 내부 예외, invalid credential config, provider generic exception | `500 generation.failed` | `status="failed"` | `rag.answer.failed` | raw credential, `encrypted_config`, API key, provider raw error 미노출 |
| provider timeout | `504 provider.timeout` | `status="failed"` | `rag.answer.failed` | `answer_run_id`, `correlation_id`만 safe details로 포함 |
| stream retrieval timeout | terminal `error` event, `reason_code="stream.timeout"` | `status="failed"` | `rag.answer.failed` | `retryable=true`; stream 시작 후에는 HTTP status를 바꾸지 않는다 |
| terminal status 전 stream client disconnect/cancel | 연결 종료 | `status="cancelled"` | `rag.answer.cancelled` | `requested/running` 상태에서만 cancelled로 마감한다. 이미 `completed/failed/blocked`로 마감된 뒤 delivery 중 끊기면 기존 status/audit을 유지한다 |

Retrieval 결과가 0건인 경우는 실패가 아니라 성공 경로다. 서버는 `rag.retrieve` audit을 남기고 `rag_answer_runs.status="completed"`로 종료한다. 이때 `citations=[]`, `retrieval_summary.retrieved_chunk_count=0`, `retrieval_summary.raw_content_returned=false`가 되어야 하며, LLM provider 호출, `llm.call` audit, `LLMUsageLog` 생성은 하지 않는다. User-facing `usage_summary`는 zero usage를 반환하고, durable/internal `usage_summary`에는 실행 시점의 model/credential 식별자 snapshot만 보존할 수 있다.

RAG 확장 3단계의 API 범위는 answer 생성과 streaming이다. `rag_answer_runs` list/detail/delete/purge API는 3단계 기본 범위에 포함하지 않으며, 필요하면 조회 권한, retention, 삭제 정책을 별도 API 계약으로 확정한다.

## 기본 Chunking 값

`POST /api/v1/rag/upload`와 document preview/process API의 기본값은 서로 다르다.

### `POST /api/v1/rag/upload` Form 기본값

| Field | Default |
| --- | --- |
| `topK` | `5` |
| `similarity` | `0.7` |
| `chunkSize` | `1000` |
| `chunkOverlap` | `200` |
| `chunkingMode` | `flat` |

### `DocumentPreviewRequest` / process 기본값

| Field | Default |
| --- | --- |
| `chunk_size` | `500` |
| `chunk_overlap` | `50` |
| `segment_identifier` | `\n\n` |
| `remove_urls_emails` | `false` |
| `remove_whitespace` | `true` |
| `strategy` | `general` |
| `source_type` | `FILE` |
| `selection_mode` | `all` |
| `chunking_mode` | `flat` |

이 값은 구현 기본값이다. 저장량, retrieval 품질, 재색인 비용에 큰 영향을 주는 정책으로 확정하면 별도 ADR 후보로 올린다.

`chunkingMode`/`chunking_mode`는 저장 또는 preview 시 chunk 생성 방식을 선택한다. 허용값은 `flat`, `hierarchical`이다. `DocumentPreviewRequest` 계열 JSON 입력은 `chunkingMode` alias를 허용하되, service layer 이후 내부 표준 field name은 `chunking_mode`다. MBA-85 2단계 1차는 고급 튜닝 입력인 `parentChunkSize`, `childChunkSize`, `childChunkOverlap`, arbitrary depth 설정을 공개 API로 열지 않는다. Child chunk는 기존 `chunkSize`/`chunkOverlap` 또는 `chunk_size`/`chunk_overlap` 값을 재사용하고, parent routing chunk 기본 크기는 implementation decision log에 기록된 내부 기본값을 따른다.

## Source Type 처리

현재 `POST /api/v1/rag/upload`는 `FILE`, `API`, `DB` source type을 처리한다.

| Source Type | 입력 | 저장되는 주요 metadata | 권한 기준 |
| --- | --- | --- | --- |
| `FILE` | `file` 또는 `s3FileUrl` + `s3FileKey` | `upload_method`, S3 key 등 | authenticated user |
| `API` | `apiUrl`, `apiMethod`, `apiHeaders`, `apiBody` | encrypted headers, parsed body | authenticated user |
| `DB` | `connectionId` | connection id/type/name | 현재 코드 기준 `connections.user_id == current_user.id` |

MBA-85 hierarchical ingestion 지원 범위:

| Source Type | `chunkingMode=flat` | `chunkingMode=hierarchical` |
| --- | --- | --- |
| `FILE` | 지원 | 지원 |
| `API` | 지원 | 지원 |
| `DB` | 지원 | 미지원. `400 unsupported_chunking_mode_for_source` |

DB source는 table, row, join result가 이미 구조화된 데이터이므로 FILE/API의 document section hierarchy와 같은 의미로 다루지 않는다. DB row를 parent로 보고 row 내부 child를 만드는 설계는 후속 이슈로 분리한다. Workflow Engine DB sync와 shared `VectorStoreService` 경로는 MBA-85에서 flat-only로 유지하며, DB source의 저장 mode가 `hierarchical`이면 flat으로 조용히 downgrade하지 않고 `unsupported_chunking_mode_for_source`에 해당하는 실패로 닫는다.

새 KB를 동시에 생성하는 upload 요청은 `embeddingModel`이 필수다. 기존 KB에 추가하는 요청은 기존 KB의 `embedding_model`을 사용한다. 현재 기존 `knowledgeBaseId` 경로는 KB 존재 여부만 확인하고 current user owner/scope를 확인하지 않는다.

현재 KB 생성과 upload 기반 신규 KB 생성은 `get_user_primary_organization_id`로 첫 active organization membership의 organization을 저장한다. 명시적인 `X-Organization-Id` header를 받는 active organization 방식은 RAG search-test `chat`/`pure`에 먼저 적용되어 있으며, 나머지 Knowledge/RAG 생성/문서 API는 아직 primary organization fallback 또는 owner/current-user scope를 사용한다.

MBA-75/MVP 2 목표 계약에서 Knowledge/RAG org-scoped API는 `X-Organization-Id` header를 사용한다. Header가 없으면 `400 organization.required`, header organization이 KB `organization_id`와 다르거나 요청 user scope 밖이면 `404 resource.not_found`로 숨긴다. Org-scoped RAG는 KB의 `organization_id`가 반드시 있어야 하며, legacy KB의 `organization_id`가 `null`이면 요청 header organization으로 보정하지 않고 backfill/reassignment 전까지 scope 밖 resource로 처리한다. Header 도입 전 primary organization fallback은 current behavior 호환 경로일 뿐 장기 계약이 아니다. Active organization membership만으로 KB `read`/`use`를 허용하지 않으며, 실제 허용은 organization manager override와 KB effective permission으로 판정한다.

현재 RAG endpoint의 owner 검증은 일관적이지 않다. `DELETE /rag/document/{document_id}`는 `KnowledgeBase.user_id == current_user.id`를 확인하지만, `analyze`, `confirm`, `progress`, 기존 KB upload 경로는 document/KB id 중심으로 동작한다. 이 차이는 MVP 2의 KB permission enforcement에서 정렬해야 한다.

## Preview / Process Hierarchy 계약

`DocumentPreviewRequest`와 이를 상속하는 process request는 `chunking_mode`를 받을 수 있다. `chunking_mode=hierarchical`은 저장된 `Document.source_type`이 `FILE` 또는 `API`인 문서에서만 지원한다. 저장된 `Document.source_type=DB` 문서에 hierarchical chunking을 요청하면 request body의 `source_type` 값과 무관하게 `400 unsupported_chunking_mode_for_source`를 반환한다.

Preview response schema는 MBA-85 2단계 1차에서 확장하지 않는다. `DocumentPreviewResponse.segments[]`는 기존처럼 `content`, `token_count`, `char_count` 중심이며, hierarchical preview에서도 최종 evidence 단위인 child chunk content만 반환한다. Parent routing chunk는 preview `segments[]`의 독립 content 항목으로 노출하지 않는다. `chunk_level`, `parent_chunk_id`, `hierarchy_path` preview 노출은 후속 schema 확장으로 분리한다.

`chunkingMode=hierarchical` 또는 `chunking_mode=hierarchical`과 `selection_mode=range` 조합은 hierarchy parent/child 번호와 기존 flat chunk 번호 의미가 충돌하므로 `400 invalid_chunking_selection`으로 거부한다.

Upload에서 받은 `chunkingMode`는 `documents.meta_info.chunking_mode`에 저장한다. `POST /api/v1/knowledge/{kb_id}/documents/{document_id}/process`가 `chunking_mode`를 받으면 같은 field를 갱신한 뒤 처리한다. `POST /api/v1/rag/document/{document_id}/confirm`과 resume processing 경로는 request body가 아니라 저장된 `documents.meta_info.chunking_mode`를 사용한다. Confirm/resume에서 `strategy`를 갱신하더라도 `chunking_mode`는 덮어쓰지 않는다. Document sync는 DB source flat-only 경로이므로 저장된 mode가 `hierarchical`이면 실패 처리하고 기존 chunk를 보존한다.

재처리 skip은 원문 `content_hash`와 embedding model만으로 판단하지 않는다. MBA-85 이후 process/confirm/resume 경로는 `chunking_mode`, `hierarchy_version`, `chunk_size`, `chunk_overlap`, `segment_identifier`, preprocess flag, selection setting을 포함한 chunking fingerprint가 기존 저장 fingerprint와 같을 때만 skip할 수 있다. Fingerprint가 없거나 달라지면 같은 원문이라도 재처리한다.

## MBA-75/MBA-78/MBA-85 Search Contract

`SearchQuery`는 기존 `query`, `top_k`, `knowledge_base_id`, `generation_model` request shape를 깨지 않는 optional field로 metadata-aware/hierarchical retrieval 계약을 확장한다.

Request extension:

| Field | Type | 설명 |
| --- | --- | --- |
| `metadata_filter` | `MetadataFilter \| null` | allowlist 기반 metadata filter. Free-form dict, JSONPath, raw SQL fragment는 허용하지 않음 |
| `classification_filter` | `string[] \| null` | `public`, `internal`, `confidential`, `pii` 중 선택 |
| `tags` | `TagFilter \| null` | `{ "mode": "contains_any" \| "contains_all", "values": string[] }`. `string[]` shorthand는 허용하지 않음 |
| `source_type` | `("FILE" \| "API" \| "DB")[] \| null` | source type filter |
| `effective_at` | datetime | `effective_from <= effective_at < effective_to` time window filter. 비교 기준은 UTC ISO 문자열(`YYYY-MM-DDTHH:MM:SS+00:00`) |
| `hierarchy_mode` | `auto/flat/parent_child` | 기존 KB는 `auto`에서 flat fallback 가능. MBA-85부터 hierarchy data가 있는 KB는 parent-child retrieval을 사용할 수 있음 |

`metadata_filter`와 top-level convenience field가 함께 오면 동일 key 중복을 validation error로 거부한다.

중복 validation:

| Top-level field | Canonical metadata key | 중복 조건 |
| --- | --- | --- |
| `classification_filter` | `metadata_filter.classification` | 둘 다 있으면 validation error |
| `tags` | `metadata_filter.tags` | 둘 다 있으면 validation error |
| `source_type` | `metadata_filter.source_type` | 둘 다 있으면 validation error |
| `effective_at` | `metadata_filter.effective_at` | 둘 다 있으면 validation error |

`hierarchy_mode`는 metadata key가 아니라 retrieval mode다. `metadata_filter.hierarchy_mode`는 허용하지 않는다.

`effective_from`/`effective_to`의 canonical 비교 값은 `documents.meta_info`에 저장된 UTC ISO 문자열(`YYYY-MM-DDTHH:MM:SS+00:00`)이다. 값이 비어 있거나 누락되면 열린 구간으로 해석하고, 값이 있지만 이 형식을 따르지 않으면 filter match에서 제외해 fail-closed로 처리한다. `document_chunks.metadata`에 복제된 값은 denormalized cache/citation evidence이며, MBA-78 1차 filter source로 보지 않는다. `Z`, offset이 다른 timestamp, 날짜 전용 문자열은 ingest/backfill 단계에서 `+00:00` 형식으로 정규화해야 한다.

MBA-78 1차 구현에서 `hierarchy_mode=auto`와 `flat`은 기존 flat retrieval을 사용하고, 명시적 `parent_child` 요청은 hierarchy data/index가 아직 없으면 `422 hierarchy_unavailable`로 닫는다. MBA-85 2단계 이후 mode별 계약은 다음과 같다.

| `hierarchy_mode` | hierarchy data 있음 | hierarchy data 없음 |
| --- | --- | --- |
| `auto` | parent-child retrieval과 flat fallback 후보를 함께 사용 | flat retrieval |
| `flat` | parent routing chunk를 제외하고 child/flat evidence chunk를 flat하게 검색 | flat retrieval |
| `parent_child` | parent-child retrieval과 필요한 flat fallback 후보를 함께 사용 | `422 hierarchy_unavailable` |

Parent routing chunk는 coarse retrieval에만 사용하고 최종 `ChunkPreview` evidence로 반환하지 않는다. 최종 evidence는 child chunk 또는 legacy/flat chunk 기준이다.

최종 ranking score는 child 또는 flat/legacy evidence chunk의 직접 vector/keyword/rerank score 기준이다. Parent route score는 parent 후보 pool 제한, tie-break, 제한적인 boost 용도로만 사용하며, child score나 flat fallback score와 정규화 없이 단순 합산하지 않는다.

Metadata filter의 canonical source는 `documents.meta_info`다. Parent routing 단계는 document-level filter를 기준으로 후보를 줄이고, child-only chunk metadata 때문에 parent 후보가 먼저 탈락하지 않도록 한다. `document_chunks.metadata`는 denormalized cache/citation evidence이며, child/flat evidence 단계의 safe fallback으로만 사용할 수 있다.

`chunk_level` 값은 `parent`, `child`, `flat`, `NULL`만 의미를 갖는다. 알 수 없는 `chunk_level` row는 evidence 후보에서 제외하고, application layer에서 warning/metric 대상으로 다룬다. Unknown 값을 `flat`으로 해석하지 않는다.

`TagFilter` validation:

- `mode`는 `contains_any` 또는 `contains_all`만 허용한다.
- `values`는 비어 있으면 validation error로 거부한다.
- tag 값은 trim 후 빈 문자열이면 거부하고, 비교 정규화는 소문자 기준으로 한다.
- 중복 tag는 정규화 후 하나로 합산한다.
- 구현 기본값은 최대 20개 tag, tag 하나당 최대 64자다.
- top-level `tags`와 `metadata_filter.tags`가 함께 오면 중복 조건으로 보고 validation error로 거부한다.

Response extension:

| Field | 위치 | 설명 |
| --- | --- | --- |
| `chunk_id` | `ChunkPreview` | 검색된 chunk id |
| `parent_chunk_id` | `ChunkPreview` 또는 trace payload | parent-child hierarchy에서 상위 chunk id. flat chunk는 `null` 가능 |
| `rank` | `ChunkPreview` | 최종 ranking 순서 |
| `score` | `ChunkPreview` 또는 trace payload | 최종 ranking score. 기존 `similarity_score`는 response 호환 필드로 유지 |
| `token_count` | `ChunkPreview` 또는 trace payload | chunk token 수. 없으면 `null` 가능 |
| `metadata_summary` | `ChunkPreview` 또는 trace payload | redaction-safe metadata summary |
| `hierarchy_path` | `ChunkPreview` | section path, heading, parent/child 정보 |

`ChunkPreview.metadata`는 기존 UI 호환 필드로 유지하지만, MBA-78 1차부터 `metadata_summary`와 같은 redaction-safe summary만 담는다. Full `documents.meta_info` 또는 `document_chunks.metadata` 원문은 search-test response에 그대로 반환하지 않는다.

Search-test response는 retrieval과 content preview를 수행하므로 KB `use` 권한을 통과한 user에게만 chunk `content` preview를 반환할 수 있다. KB 목록, 상세, document metadata 조회는 `read` 권한 기준으로 분리한다. Workflow trace/run detail 기본 응답은 raw chunk content 없이 citation metadata만 반환해야 한다.

## MBA-75 Trace/Citation Contract

RAG retrieval 전용 table은 만들지 않는다. Workflow runtime의 per-chunk retrieval evidence는 `trace_payloads.payload_kind='rag.retrieval'`의 redacted payload convention으로 저장하고, run/node trace metadata에는 redaction-safe summary allowlist만 저장한다. Standalone Agent answer는 workflow run이 없을 수 있으므로 `trace_payloads`에 RAG answer 전용 FK를 추가하지 않고, `rag_answer_runs`의 summary/citation allowlist와 `correlation_id`를 사용한다. 성공적인 retrieval의 audit event는 `audit_logs.action='rag.retrieve'`로 기록하고, 아래 `payload_kind='rag.retrieval'`은 trace payload 분류값으로만 사용한다.

권장 trace payload record:

```json
{
  "payload_kind": "rag.retrieval",
  "workflow_node_run_id": "uuid",
  "redacted_payload": {
    "knowledge_base_ids": ["uuid"],
    "workflow_run_id": "uuid",
    "node_id": "llm-node-id",
    "retrieved_chunks": [
      {
        "document_id": "uuid",
        "chunk_id": "uuid",
        "parent_chunk_id": "uuid",
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

Runtime node가 logger로 넘기는 payload body에는 `workflow_node_run_id`를 넣지 않는다. `WorkflowLogger`가 저장 시 `trace_payloads.workflow_node_run_id` 컬럼으로 연결한다.

Run/node trace metadata allowlist는 `knowledge_base_id`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, score summary, hierarchy fallback flag, `raw_content_returned` 같은 요약 field로 제한한다. `retrieved_chunks` 배열, raw chunk content, prompt/completion, provider raw response는 run/node metadata에 복사하지 않는다.

현재 `TraceMetadataSanitizer`는 run/node RAG metadata에서 `knowledge_base_id`, `retrieved_chunk_count`, `document_ids`, `citation_ids`, `score_summary`, `hierarchy_fallback`, `raw_content_returned`, `latency_ms`, `retrieval_payload_id`, `retrieved_context_payload_id` 같은 summary field만 허용한다. MBA-85는 run/node metadata allowlist를 넓히지 않는다. `hierarchy_mode`, parent/child candidate count 같은 diagnostic 값이 필요하면 `trace_payloads.payload_kind='rag.retrieval'`의 redacted payload 안에만 둔다. Legacy `retrieval_results` 입력은 저장하지 않고 summary field로 변환한다. Raw chunk content와 `retrieved_chunks` 배열은 run/node metadata allowlist에 포함하지 않는다.

Trace/audit metadata에는 raw chunk content, raw prompt, credential 원문, API key, token, encrypted_config, secret value, provider raw response를 저장하지 않는다. `credential_id` 같은 식별자는 권한 보호된 trace 응답 whitelist 안에서만 허용할 수 있다.

## MVP 2 변경 기준

- 현재 코드의 KB endpoint는 주로 owner/current-user scope다. RAG search-test `chat`/`pure`와 Workflow Engine runtime retrieval은 KB `use` 권한 평가를 시작했지만, 나머지 Knowledge/RAG endpoint scope 정렬은 후속 범위다.
- RAG node runtime은 knowledge base `use` 권한을 평가한다.
- RAG search-test `chat`/`pure`는 retrieval과 content preview를 수행하므로 knowledge base `use` 권한을 평가한다. 단순 KB/detail/document metadata 조회는 `read` 권한 기준이다.
- `user_knowledge_permissions`는 현재 코드에 없으며 MVP 2에서 추가할 목표 table이다.
- document별 permission table은 만들지 않는다.
- document classification과 re-index flag는 `documents.meta_info` metadata convention으로 저장한다.
- Workflow runtime RAG retrieval trace는 `rag_retrieval_traces` 신규 table이 아니라 trace payload/run metadata로 저장한다. Per-chunk evidence는 `trace_payloads`, run/node metadata는 summary allowlist로 분리한다.
- Standalone RAG Agent answer는 `rag_answer_runs`와 opaque `correlation_id`로 trace/usage/audit을 연결한다. `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id` 같은 RAG 전용 FK는 만들지 않는다.
- Metadata filter는 allowlist 기반 schema로만 받는다. Metadata는 permission source of truth가 아니다.
- `policy.warn`/`policy.block` document metadata enforcement는 MBA-78 기준 후속 구현 범위다. RAG Agent answer 3단계는 external LLM prompt path의 final evidence `pii` block만 이번 범위에 포함하고, search-test/runtime 전체 policy enforcement 확장은 별도 범위다. MBA-78 1차는 action naming, KB `use` enforcement, `permission.denied`/`rag.retrieve` audit 경계를 먼저 고정한다.
- Hierarchical RAG는 nullable parent/child chunk schema와 flat fallback으로 도입한다. MBA-85 2단계는 FILE/API source의 opt-in hierarchical ingestion과 parent-child retrieval을 backend 범위에서 연결하며, DB source hierarchical chunking, frontend hierarchy UI, LLM 기반 parent summary 생성은 후속 범위다. Hierarchical parent/child 저장 경로에서 content 암호화가 실패하면 평문 fallback 없이 처리 실패로 닫고 기존 chunk를 보존한다. Hierarchical mode는 parent와 child를 모두 embedding하므로 같은 문서의 flat mode보다 처리 시간과 embedding 비용이 늘 수 있고, progress는 parent+child 저장 대상과 embedding batch 기준으로 계산한다.

## Knowledge/RAG 오류 reason

| Code | HTTP | 조건 |
| --- | --- | --- |
| `invalid_chunking_mode` | `400` | upload form의 `chunkingMode`가 `flat/hierarchical` 외 값 |
| `invalid_chunking_selection` | `400` | `chunkingMode=hierarchical` 또는 `chunking_mode=hierarchical`과 `selection_mode=range` 조합 |
| `unsupported_chunking_mode_for_source` | `400` | upload form은 `chunkingMode=hierarchical`과 `sourceType=DB` 조합, process/preview는 `chunking_mode=hierarchical`과 저장된 `Document.source_type=DB` 조합 |
| `hierarchy_unavailable` | `422` | `hierarchy_mode=parent_child` 요청에 사용할 유효 parent-child hierarchy data가 없음 |
| `invalid_correlation_id` | `400` | client가 제공한 Agent answer `correlation_id`가 길이/문자셋/보안 규칙을 만족하지 않음 |
| `credential_selection_required` | `409` | 후속 default credential/preset 확장에서 Agent answer generation credential/model을 deterministic하게 선택할 수 없음 |
| `provider.timeout` | `504` 또는 SSE terminal `error` | Agent answer provider 호출이 60초 timeout cap을 초과함. Stream 시작 후에는 HTTP status 대신 terminal event reason code로 전달 |
| `stream.timeout` | SSE terminal `error` | Agent answer SSE retrieval 단계가 30초 timeout cap을 초과함. Stream 시작 후에는 HTTP status를 바꾸지 않음 |
| `generation.failed` | `500` | Agent answer retrieval/generation 내부 예외, invalid credential config, provider generic exception 등 sanitized internal failure가 발생함. 응답에는 raw credential, `encrypted_config`, API key, provider raw error를 포함하지 않음 |
