# Tracing 및 Audit API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Related ADRs: [ADR-202606271559-audit-log-rag-trace-storage](../decisions/ADR-202606271559-audit-log-rag-trace-storage.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 범위

Workflow run trace, span, payload, trace policy, retention purge, audit log 조회 계약을 정의한다.

## Trace 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `GET` | `/api/v1/traces` | query | `TraceListResponse` | trace/audit `read` |
| Implemented | `GET` | `/api/v1/traces/{trace_id}` | 없음 | `TraceDetailSchema` | trace/audit `read` |
| Implemented | `GET` | `/api/v1/traces/{trace_id}/spans` | 없음 | `TraceSpanSchema[]` | trace/audit `read` |
| Implemented | `GET` | `/api/v1/traces/{trace_id}/payloads` | query | `TracePayloadSchema[]` | trace/audit `read` |
| Implemented | `GET` | `/api/v1/traces/{trace_id}/payloads/{payload_id}` | query | `TracePayloadSchema` | `view_raw` if raw |
| Implemented | `POST` | `/api/v1/tracing/retention/purge` | `RetentionPurgeRequest` | `RetentionPurgeResponse` | system admin |
| Implemented | `GET` | `/api/v1/tracing/policies/redaction` | query | `TracePolicyResponse` | system admin |
| Implemented | `PATCH` | `/api/v1/tracing/policies/redaction` | `TraceRedactionPolicyPatch` | `TracePolicyResponse` | system admin |
| Implemented | `GET` | `/api/v1/tracing/policies/retention` | query | `TracePolicyResponse` | system admin |
| Implemented | `PATCH` | `/api/v1/tracing/policies/retention` | `TraceRetentionPolicyPatch` | `TracePolicyResponse` | system admin |
| Implemented | `GET` | `/api/v1/tracing/policies/visibility` | query | `TracePolicyResponse` | system admin |
| Implemented | `PATCH` | `/api/v1/tracing/policies/visibility` | `TraceVisibilityPolicyPatch` | `TracePolicyResponse` | system admin |

현재 trace 조회 query:

- `GET /api/v1/traces`는 `status`, `trigger_mode`, `from`, `to`, `app_id`, `workflow_id`, `deployment_id`, `user_id`, `correlation_id`, `page`, `limit`을 지원한다. `limit` 범위는 `1..100`이고 기본값은 `20`이다.
- `GET /api/v1/traces?correlation_id=...`는 workflow trace records만 검색한다. Standalone RAG Agent answer run은 workflow trace record가 아니므로 이 list/detail API의 반환 대상이 아니며, answer run 조회/list/delete/purge 계약은 RAG API에서 별도로 정의한다.
- `GET /api/v1/traces/{trace_id}`는 `view`, `include_spans`, `include_payloads`를 지원한다.
- payload 목록은 `view`, `payload_kind`, `node_run_id`, `history`, `page`, `limit`을 지원하며 `limit` 최대값은 `1000`이다.
- Trace 목록 조회는 먼저 system admin 또는 app owner 범위로 DB 후보를 제한하고, 이후 각 run에 대해 `TraceAccessService.check_trace_access`를 다시 적용한다. 따라서 현재 목록 API는 workflow RBAC만 가진 non-owner trace를 넓게 검색하는 용도가 아니다.
- Trace 상세/payload 접근 판정은 system admin, app owner, workflow effective `auth_state`, app/organization visibility policy를 함께 사용한다.
- 위 표의 trace/audit `read`와 `view_raw`는 현재 구현에서 `team_audit_permissions`를 직접 조회한다는 뜻이 아니다. 현재 trace access control은 system admin, app owner, workflow RBAC, visibility policy 조합으로 구현되어 있고, audit permission 기반 organization-wide search/view_raw 통합은 후속 목표다.

Trace policy 및 retention purge API의 `system admin` 판정은 `TraceRbacService` provider에 위임한다. 현재 기본 provider는 deny-all이므로, 별도 RBAC provider를 설정하지 않은 런타임에서는 policy 변경과 retention purge가 `403 system_admin_required`로 차단된다. Policy schema에는 `organization` scope가 있지만 현재 management API는 `global`/`app` scope만 허용하고, organization scope 요청은 `organization_scope_policy_not_supported` 또는 `organization_scope_purge_not_supported`로 거부한다.

## Audit 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `GET` | `/api/v1/users/me/audit-logs` | pagination query | `AuditLogListResponse` | own audit read |
| Planned | `GET` | `/api/v1/audit/logs` | filter query | `AuditLogListResponse` | audit `read` |

## LLM Trace 엔드포인트

현재 코드는 run/node 기준 LLM usage 조회를 별도 endpoint로 제공한다. 기존 run detail 응답에 LLM usage 요약을 포함하는 방식은 채택하지 않았다.

| Status | Method | Path | Permission | 설명 |
| --- | --- | --- | --- | --- |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/runs/{run_id}/llm-traces` | workflow `read` | 별도 LLM trace endpoint. `node_id`, `limit`, `offset` query를 지원 |

`run_id`는 URL의 `workflow_id`에 속한 workflow run이어야 한다. 해당 workflow에 속하지 않는 run id이거나 존재하지 않는 run id이면 LLM trace를 반환하지 않는다.

중복 API를 만들지 않는다.

LLM trace 응답 whitelist:

- 허용: `id`, `workflow_id`, `workflow_run_id`, `node_id`, `model_id`, `model_name`, `provider`, `credential_id`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `total_cost`, `latency_ms`, `status`, `created_at`
- 금지: credential value, `encrypted_config` 값/content, API key, 인증 token, raw prompt, raw completion, provider raw response
- `credential_id`는 secret이 아니라 어떤 등록 credential을 사용했는지 추적하기 위한 식별자다. 해당 id로 credential 원문을 조회하는 API는 별도 권한으로 보호해야 한다.

## RAG Agent Answer Correlation

Standalone RAG Agent answer는 workflow run이 없을 수 있으므로 현재 workflow trace endpoint의 `trace_id`로 직접 조회되는 대상이 아니다. Agent answer 실행 기준은 RAG 도메인의 `rag_answer_runs`이며, trace/usage/audit과의 느슨한 연결은 opaque `correlation_id`로 한다.

`correlation_id`는 trace list와 answer run을 같은 DB FK로 묶지 않는다. 같은 값으로 workflow trace와 standalone answer를 운영상 대조할 수는 있지만, trace API는 `rag_answer_runs`를 join하거나 반환하지 않는다.

현재 `trace_payloads`는 `workflow_run_id`가 필수인 workflow trace payload table이다. 따라서 standalone answer payload를 저장하기 위해 `trace_payloads.rag_answer_run_id`를 추가하지 않는다. Workflow runtime에서 발생한 RAG evidence는 기존처럼 `trace_payloads.payload_kind='rag.retrieval'`로 저장한다.

현재 `llm_usage_logs`는 workflow/run이 nullable인 LLM usage 원천이지만 generic `correlation_id` field를 갖고 있지 않다. RAG 도메인은 `llm_usage_logs.rag_answer_run_id`를 추가하지 않는다. Standalone answer usage와 answer run을 정확히 연결해야 하면 usage 도메인이 generic `correlation_id` 또는 usage metadata extension을 별도 설계해야 한다. 그 전까지 Agent answer의 `usage_summary`는 실행 시점의 denormalized snapshot이며, workflow trace/usage 조회 API가 강한 join lineage를 제공한다고 보지 않는다. 이 snapshot은 token/cost/latency와 model/credential/provider 식별자 allowlist로 제한한다.

Client supplied `correlation_id`는 추적용 힌트다. 권한 판정, scope 판정, resource 조회 key로 사용하지 않으며, secret/token/API key/prompt 원문을 포함하면 안 된다. 값이 없으면 서버가 생성하고, 제공된 값이 길이/문자셋/보안 규칙을 만족하지 않으면 `400 invalid_correlation_id`로 거부한다.

Agent answer correlation API를 도입하더라도 기본 응답은 redaction-safe answer summary, retrieval summary, citation summary, usage aggregate만 반환한다. Raw retrieved chunk content, raw prompt/completion, credential 원문, API key, token, encrypted_config, provider raw response는 trace/audit/usage metadata에 저장하거나 반환하지 않는다.

## Raw Payload 규칙

- raw payload는 system admin, app owner, workflow effective `manager` 수준 RBAC 중 하나와 trace visibility policy가 모두 허용해야 반환한다.
- app owner와 RBAC 사용자는 owner visibility flag(`owner_trace_access_enabled`, `owner_redacted_payload_access_enabled`, `owner_raw_payload_access_enabled`, prompt/completion flag)를 따른다.
- raw payload 접근 시도는 응답 전에 `trace_payload_access_events`에 남긴다. 감사 기록 실패 시 허용된 raw 응답은 실패한다.
- secret, token, credential 원문은 audit metadata에 저장하지 않는다.
- `ViewLevel`은 현재 schema 기준 `metadata`, `redacted`, `raw`만 허용한다.
