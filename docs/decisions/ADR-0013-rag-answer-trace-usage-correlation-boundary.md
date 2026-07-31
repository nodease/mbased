# ADR-0013: RAG Agent Answer Trace/Usage Correlation Boundary

Status: Accepted
Date: 2026-07-01 02:20 KST
Original: ADR-202607010220-rag-answer-trace-usage-correlation-boundary
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related ADRs: [ADR-0004-audit-log-rag-trace-storage](ADR-0004-audit-log-rag-trace-storage.md), [ADR-0008-audit-action-naming-standard](ADR-0008-audit-action-naming-standard.md), [ADR-0012-metadata-aware-hierarchical-rag-boundary](ADR-0012-metadata-aware-hierarchical-rag-boundary.md)

## Context

MBA-75와 MBA-85는 workflow runtime에서 발생한 RAG retrieval evidence를 `trace_payloads.payload_kind='rag.retrieval'` convention으로 저장하고, run/node metadata에는 redaction-safe summary만 남기는 경계를 확정했다. 이 경계는 `trace_payloads.workflow_run_id`가 필수인 현재 trace 모델과 잘 맞는다.

RAG 확장 3단계에서는 사용자가 프론트엔드 UI에서 직접 질문하고, Agent가 metadata-aware 및 hierarchical RAG로 context chunk를 찾은 뒤 답변하는 독립 실행 흐름이 필요하다. 이 흐름은 workflow run이 없을 수 있지만, 운영자는 답변 품질, citation, retrieval summary, LLM token/cost/latency를 함께 추적하고 싶어 한다.

`trace_payloads.rag_answer_run_id` 또는 `llm_usage_logs.rag_answer_run_id` 같은 RAG 전용 FK를 trace/usage table에 직접 추가하면 RAG 도메인이 trace/usage 도메인의 물리 모델을 침범한다. 반대로 standalone answer를 임의의 workflow run으로 위장하면 trace access, retention, payload access audit 정책이 잘못 적용될 수 있다.

## Decision

RAG Agent answer 실행의 기준 record는 RAG 도메인이 소유하는 `rag_answer_runs`로 둔다. `rag_answer_runs`는 organization, actor, target KB, answer status, redaction-safe retrieval summary, citation summary, answer summary, nullable top-level answer hash, policy result, generation summary, denormalized usage snapshot, `correlation_id`를 저장하는 실행 anchor다.

Trace/usage 도메인과의 연결은 RAG 전용 FK가 아니라 opaque `correlation_id`로 한다. `correlation_id`는 request/answer 실행 단위를 식별하는 application-level convention이며, 참조 무결성을 보장하는 DB FK가 아니다. 같은 값은 `rag_answer_runs.correlation_id`, `audit_logs.audit_metadata.correlation_id`, workflow run이 있는 경우 `workflow_runs.correlation_id`, workflow trace payload redaction metadata 또는 usage-domain correlation field에 기록할 수 있다. Client가 제공한 `correlation_id`는 권한 판정이나 tenant scope 판정에 사용하지 않고, 길이/문자셋/secret 금지 규칙을 통과하지 못하면 거부한다. `correlation_id`는 전역 unique resource key가 아니며, server-generated 값은 answer run 단위로 충분히 고유하게 생성하고 client supplied 값은 grouping key로만 취급한다. 조회 최적화가 필요하면 `(organization_id, correlation_id, created_at)` index를 사용한다.

`trace_payloads`는 현재와 같이 workflow run 또는 workflow node run에 연결되는 trace payload 저장소로 유지한다. Workflow runtime RAG evidence는 계속 `trace_payloads.payload_kind='rag.retrieval'`에 저장한다. Standalone Agent answer는 현재 trace 모델에 workflow run이 없으므로 `trace_payloads`에 직접 저장하지 않는다. Standalone answer의 per-answer summary와 citation은 `rag_answer_runs`에 저장하고, trace 도메인이 추후 generic trace subject 또는 correlation-only payload를 공식 지원할 때 별도 설계로 연결한다.

`llm_usage_logs`는 LLM token/cost/latency의 원천으로 유지한다. RAG 도메인은 `llm_usage_logs.rag_answer_run_id` 같은 RAG 전용 FK를 추가하지 않는다. Standalone answer usage를 answer run과 정확히 묶어야 하면 usage 도메인이 generic `correlation_id` 또는 usage metadata field를 제공하는 별도 migration을 설계한다. 그 전까지는 `rag_answer_runs.usage_summary`를 실행 시점의 denormalized snapshot으로만 보고, answer summary와 LLM usage 원천을 강한 FK로 조인할 수 있다고 문서화하지 않는다.

Raw user question, raw final answer, raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response는 `rag_answer_runs`, audit metadata, trace metadata, usage metadata에 기본 저장하지 않는다. 저장해야 할 값은 redaction-safe answer summary, nullable top-level answer hash, citation id, document/chunk id, score summary, token/cost aggregate snapshot, policy result로 제한한다. `retrieval_summary`는 KB id, hierarchy mode, retrieved chunk count, document/citation ids, score summary, latency, `raw_content_returned=false` 같은 값으로 제한한다. `citation_summary`는 citation id, document id, chunk id, rank, score, filename, heading, hierarchy path, safe metadata summary로 제한하고 chunk content와 user-facing `content_preview`를 포함하지 않는다. `answer_hash`는 nullable `rag_answer_runs.answer_hash` top-level column을 canonical 위치로 두고, `answer_summary`는 length, cited document count, citation ids, policy result, completion status, 선택적 redacted summary로 제한한다. `usage_summary`는 `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_cost`, `latency_ms`, `model_id`, `model_name`, `provider`, `credential_id` 같은 집계/식별자 allowlist로 제한한다. Query/answer hash를 저장할 때는 HMAC-SHA256, server-side secret/pepper, `hash_version`을 사용해야 하며, HMAC secret/pepper가 없으면 값을 저장하지 않고 unsalted hash fallback을 허용하지 않는다. Answer history/replay가 필요하면 raw provider response가 아니라 별도 redacted answer snapshot, retention, access control을 공식 문서와 ADR로 먼저 확정한다.

Agent answer lifecycle audit은 `rag.answer.requested`, `rag.answer.completed`, `rag.answer.failed`, `rag.answer.cancelled`를 사용한다. 이 action은 사용자-facing answer 실행 단위의 운영 이벤트이며, retrieval 성공 감사 `rag.retrieve`, provider 호출 감사 `llm.call`, answer 상태 `rag_answer_runs.status`를 대체하지 않는다. `rag.answer.requested`는 schema validation, organization header validation, active organization scope 확인, KB scope visibility 확인, required credential/model visibility 확인을 모두 통과해 answer run row를 생성할 때 기록한다. Resource hiding 대상인 `resource.not_found`, scope 밖, organization mismatch에는 answer run과 lifecycle audit을 만들지 않는다. Invalid organization header와 validation 실패처럼 실행 전 검증에서 닫히는 오류도 answer run과 lifecycle audit 없이 반환한다. Scope 안 resource가 확인된 뒤 policy 또는 permission preflight로 answer delta를 만들지 못한 경우에만 `rag_answer_runs.status="blocked"`를 사용한다. PII/classification/metadata policy 차단은 `policy.block`, KB/credential/model permission preflight 차단은 `permission.denied` audit으로 표현하고, 별도 `rag.answer.blocked` action은 만들지 않는다.

## Options Considered

1. `trace_payloads`와 `llm_usage_logs`에 `rag_answer_run_id`를 추가한다.
   - 장점: SQL join이 단순하다.
   - 단점: RAG 도메인 FK가 trace/usage 물리 모델에 침투하고, workflow 중심 trace access/retention 정책과 standalone answer 정책이 섞인다.

2. Standalone Agent answer도 임시 workflow run을 만들어 trace에 저장한다.
   - 장점: 기존 trace payload 저장소를 재사용할 수 있다.
   - 단점: 실제 workflow 실행이 아닌 값을 workflow trace처럼 보이게 하며, app/workflow RBAC와 audit semantics가 왜곡된다.

3. RAG-owned answer run과 opaque correlation id로 연결한다.
   - 장점: 도메인 경계를 유지하고, trace/usage 도메인이 generic correlation 기능을 확장할 수 있다.
   - 단점: DB FK 수준의 강한 lineage는 제공하지 않는다.

## Consequences

- RAG 확장 3단계의 Agent answer API는 `rag_answer_runs`를 중심으로 설계한다.
- Workflow RAG runtime의 per-chunk evidence 저장 방식은 바꾸지 않는다.
- Standalone Agent answer의 trace/usage 통합은 correlation convention까지가 기본 계약이다.
- Standalone Agent answer retention purge aggregate는 `rag.answer.purge` audit action으로 기록한다. 기본 aggregate event는 `target_type='rag_answer_runs'`, `target_id=null`로 두고, metadata는 `organization_id`, `cutoff`, `purged_count`, `failed_count`, `retryable`, `status` allowlist로 제한한다.
- Standalone Agent answer의 조회/list/delete/purge API는 이 ADR에서 자동 승인하지 않는다. 필요하면 API 문서에서 권한, retention, 삭제 정책을 별도로 확정한다.
- Strong FK lineage, cross-domain usage join, generic trace subject가 필요하면 trace/usage 도메인 설계 이슈로 분리한다.
- PR이나 구현 계획에서 `trace_payloads.rag_answer_run_id`, `llm_usage_logs.rag_answer_run_id`, standalone answer 전용 `rag_retrieval_traces`를 추가하는 방향은 이 ADR과 충돌한다.

## Follow-up

- `rag_answer_runs` schema를 도입할 때는 raw content 저장 금지, citation summary allowlist, status transition, retention, access control을 함께 문서화한다.
- Usage 도메인에서 standalone request 단위 비용 추적이 필요하면 generic `correlation_id` 또는 usage metadata extension을 별도 ADR로 다룬다.
- Trace 도메인에서 workflow 외 실행 payload 저장이 필요하면 generic trace subject 또는 correlation-only payload 저장소를 별도 ADR로 다룬다.
