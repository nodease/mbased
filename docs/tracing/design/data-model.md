# Tracing 데이터 모델 설계

## 목적

Tracing 1차 전체 구현에 필요한 데이터 모델 변경을 정의한다.

전체 시스템 관점의 모델은 `../../data-model/system-data-model-design.md`를 우선 참조한다. 이 문서는 tracing 구현에 직접 필요한 모델만 상세화한다.

## 모델링 원칙

- `WorkflowRun`은 domain trace이다.
- `WorkflowNodeRun`은 domain span이다.
- 별도 `trace_id` 컬럼은 추가하지 않는다.
- `workflow_runs.app_id`를 추가해 trace 조회와 app owner 판별을 단순화한다.
- raw payload와 redacted payload를 분리 저장한다.
- 기존 run/node `inputs`, `outputs`는 호환성을 위해 유지하되 redacted copy만 저장한다.
- 정책은 DB 모델로 관리한다.
- JSONB는 확장성을 위해 사용하되, 자주 필터링할 필드는 명시 컬럼으로 둔다.
- `trace_metadata`는 scope별 allowlist sanitizer를 통과한 안전한 관측값만 저장한다.
- 원문성 값은 metadata가 아니라 `trace_payloads`에 저장하고 접근제어·redaction·retention 정책을 적용한다.

## Trace 모델

기준 모델: `apps/shared/db/models/workflow_run.py`의 `WorkflowRun`

기존 필드 사용:

- `id`: domain trace id
- `workflow_id`
- `user_id`: 실행자
- `deployment_id`
- `workflow_version`
- `status`
- `trigger_mode`
- `inputs`: redacted compatibility copy
- `outputs`: redacted compatibility copy
- `error_message`
- `started_at`
- `finished_at`
- `duration`
- `meta_info`
- `total_tokens`
- `total_cost`

추가 필드:

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `app_id` | `UUID`, nullable, index | trace가 속한 app |
| `correlation_id` | `String`, nullable, index | 외부 요청/system trace 연결 |
| `request_id` | `String`, nullable, index | Gateway request id |
| `workflow_task_id` | `String`, nullable | Celery workflow task id |
| `trace_metadata` | `JSONB`, nullable | trace-level 관측 metadata |
| `redaction_applied` | `Boolean`, default false | redaction 적용 여부 |
| `pii_detected` | `Boolean`, default false | PII 탐지 여부 |
| `redaction_policy_id` | `UUID`, nullable | 적용 redaction policy |
| `retention_policy_id` | `UUID`, nullable | 적용 retention policy |
| `visibility_policy_id` | `UUID`, nullable | 적용 visibility policy |
| `payload_storage_mode` | `String`, default `redacted_only` | `none`, `redacted_only`, `raw_and_redacted` |

## Span 모델

기준 모델: `apps/shared/db/models/workflow_run.py`의 `WorkflowNodeRun`

기존 필드 사용:

- `id`: domain span id
- `workflow_run_id`
- `node_id`
- `node_type`
- `status`
- `inputs`: redacted compatibility copy
- `process_data`: 실행 시점 설정 스냅샷
- `outputs`: redacted compatibility copy
- `error_message`
- `started_at`
- `finished_at`

추가 필드:

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `duration` | `Float`, nullable | span 실행 시간. 초 단위 |
| `trace_metadata` | `JSONB`, nullable | span-level 관측 metadata |
| `redaction_applied` | `Boolean`, default false | redaction 적용 여부 |
| `pii_detected` | `Boolean`, default false | PII 탐지 여부 |
| `redaction_policy_id` | `UUID`, nullable | 적용 redaction policy |
| `parent_node_run_id` | `UUID`, nullable | nested workflow/submodule span 연결 |
| `sequence` | `Integer`, nullable | trace 내부 관측 순서 |
| `retry_count` | `Integer`, default 0 | 재시도 횟수 |

## Payload 모델

### `TracePayload`

신규 테이블: `trace_payloads`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | payload id |
| `workflow_run_id` | `UUID`, FK, index | trace id |
| `workflow_node_run_id` | `UUID`, FK nullable, index | span payload면 span id |
| `scope` | `String`, index | `trace`, `span` |
| `payload_kind` | `String`, index | payload 종류 |
| `sequence` | `Integer`, nullable | 같은 trace/span 안에서 payload가 관측된 순서 |
| `attempt` | `Integer`, default 1 | retry 또는 재실행 attempt 번호 |
| `redacted_payload` | `JSONB`, nullable | redacted copy |
| `raw_payload_encrypted` | `Text`, nullable | encrypted raw payload |
| `redaction_applied` | `Boolean`, default false | redaction 적용 여부 |
| `pii_detected` | `Boolean`, default false | PII 탐지 여부 |
| `secret_detected` | `Boolean`, default false | secret 탐지 여부 |
| `redaction_metadata` | `JSONB`, nullable | field path, rule id, detector type |
| `storage_mode` | `String` | `metadata_only`, `redacted_only`, `raw_and_redacted` |
| `retention_expires_at` | `DateTime`, nullable, index | payload 만료 시각 |
| `retention_purged_at` | `DateTime`, nullable, index | 비삭제 purge 처리 완료 시각 |
| `created_at` | `DateTime` | 생성 시각 |

`payload_kind` 후보:

- `input`
- `output`
- `prompt`
- `completion`
- `http_request`
- `http_response`
- `stdout`
- `stderr`
- `retrieved_context`
- `guardrail_reason`

저장 정책:

- redacted copy는 기본 저장 대상이다.
- raw payload는 기본 비활성이다.
- raw payload는 암호화된 필드에만 저장한다.
- raw payload 암호화 실패 시 raw payload는 저장하지 않고 redacted copy만 유지한다.
- secret은 raw 저장 허용 정책이 있어도 저장하지 않는다.
- `redaction_metadata`에는 값이 아니라 path와 rule id만 저장한다.
- payload 저장은 기본 append-only이다.
- 같은 trace/span/payload_kind에 새 payload가 들어오면 기존 row를 덮어쓰지 않고 새 row를 추가한다.
- API의 기본 view는 latest payload이며, history 조회는 `sequence`와 `attempt`로 정렬한다.

### `TracePayloadAccessEvent`

신규 테이블: `trace_payload_access_events`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | event id |
| `payload_id` | `UUID`, FK nullable | 조회 대상 payload |
| `workflow_run_id` | `UUID`, FK, index | trace id |
| `actor_user_id` | `UUID`, FK nullable | 요청 사용자. 사용자 삭제 시 감사 row는 유지하고 null 처리 |
| `actor_user_ref` | `String`, nullable, index | secret 기반 HMAC 단방향 actor reference |
| `view_level` | `String` | `metadata`, `redacted`, `raw` |
| `allowed` | `Boolean` | 허용 여부 |
| `reason_code` | `String` | 허용/차단 사유 |
| `created_at` | `DateTime` | 이벤트 시각 |

이 테이블은 `audit_logs`의 대체물이 아니라 raw payload 조회에 특화된 최소 안전장치다.

`actor_user_ref`는 `TRACE_AUDIT_ACTOR_REF_SECRET`이 있을 때만 생성한다. secret이 없으면 단순 hash로 fallback하지 않고 null로 둔다.

## Trace metadata allowlist

`trace_metadata`는 JSONB지만 임의 key 저장소가 아니다. 저장 전, Celery log task 저장 시, Trace API 반환 시 모두 scope별 sanitizer를 적용한다.

Run-level 허용 범위:

- `gateway`: `method`, `path`, `route`, `status_code`, `latency_ms`, `client_ip`, `content_type`, `request_id`, `trace_id`, `auth_latency_ms`, `deployment_lookup_latency_ms`, `celery_enqueue_latency_ms`, `user_agent_family`
- `auth`: `authenticated`, `principal_type`, `status`, `latency_ms`
- `deployment`: `deployment_id`, `workflow_version`, `status`, `lookup_latency_ms`
- `celery`: `task_id`, `queue`, `status`, `enqueue_latency_ms`
- `retention`: `purged_at`, `action`, `purged`, `dry_run`, `scope_type`, `scope_id`
- scalar: `correlation_id`, `request_id`, `trace_id`, `workflow_task_id`

Span-level 허용 범위는 node type별로 분리한다.

- LLM: `provider`, `model`, `credential_id`, token/cost/latency 요약, prompt/completion payload id
- RAG: `retrieval_results`, `retrieved_context_payload_id`, `latency_ms`
- HTTP: method/host/path/status/size/latency/retry/payload id 요약
- Sandbox: exit code, timeout, latency, stdout/stderr payload id
- Workflow: `latency_ms`
- Guardrail: decision/blocking/reason code/redacted reason/severity/latency 요약
- Error: `type`, `error_type`, `error_code`

RAG `retrieval_results`는 다음 source summary 필드만 허용한다.

```json
{
  "knowledge_base_id": "kb-id",
  "document_id": "doc-id",
  "filename": "guide.pdf",
  "page_number": 3,
  "similarity_score": 0.87
}
```

다음 key 또는 유사 pattern은 allowlist 밖에서 제거한다.

- `content`
- `text`
- `chunk`
- `body`
- `raw`
- `prompt`
- `completion`
- `request`
- `response`
- `message`
- `input`
- `output`
- `authorization`
- `cookie`
- `token`
- `secret`
- `credential`

## Policy 모델

### `TraceRedactionPolicy`

테이블: `trace_redaction_policies`

필수 필드:

- `id`
- `scope_type`: `global`, `organization`, `app`
- `scope_id`
- `redaction_enabled`
- `raw_payload_storage_enabled`
- `prompt_completion_storage_enabled`
- `pii_detection_enabled`
- `store_redacted_copy_only`
- `sensitive_headers`
- `sensitive_json_paths`
- `sensitive_keywords`
- `regex_rules`
- `replacement`
- `is_active`
- `updated_by`
- `created_at`
- `updated_at`

### `TraceRetentionPolicy`

테이블: `trace_retention_policies`

필수 필드:

- `id`
- `scope_type`
- `scope_id`
- `metadata_retention_days`
- `raw_payload_retention_days`
- `redacted_payload_retention_days`
- `prompt_completion_retention_days`
- `failed_trace_retention_days`
- `retention_action`: `delete`, `anonymize`, `summarize`
- `is_active`
- `updated_by`
- `created_at`
- `updated_at`

### `TraceVisibilityPolicy`

테이블: `trace_visibility_policies`

필수 필드:

- `id`
- `scope_type`
- `scope_id`
- `owner_trace_access_enabled`
- `owner_redacted_payload_access_enabled`
- `owner_raw_payload_access_enabled`
- `owner_prompt_completion_access_enabled`
- `admin_raw_payload_access_enabled`
- `admin_prompt_completion_access_enabled`
- `deny_owner_trace_access`
- `default_view_level`
- `is_active`
- `updated_by`
- `created_at`
- `updated_at`

정책 우선순위:

1. app
2. organization
3. global

deny 정책은 allow 정책보다 우선한다.

## App owner 판별

신규 run:

```text
workflow_runs.app_id
  -> apps.id
  -> apps.created_by
```

기존 run fallback:

```text
workflow_runs.workflow_id
  -> workflows.id
  -> workflows.app_id
  -> apps.id
  -> apps.created_by
```

배포 실행 보조 검증:

```text
workflow_runs.deployment_id
  -> workflow_deployments.id
  -> workflow_deployments.app_id
  -> apps.id
```

금지:

- `workflow_runs.user_id`를 app owner 판별에 사용하지 않는다.

## Guardrail span metadata

Moduly Guardrail node는 일반 span으로 기록한다.

`workflow_node_runs.trace_metadata.guardrail`:

```json
{
  "type": "moduly_guardrail",
  "decision": "blocked",
  "blocked": true,
  "policy_id": "policy-id",
  "rule_id": "rule-id",
  "reason_code": "pii_detected",
  "reason_redacted": "PII pattern matched in input.email",
  "severity": "high",
  "latency_ms": 12
}
```

전역 AWS Bedrock Guardrail 결과 payload는 내부 trace에 저장하지 않는다.

## Migration 전략

1차 전체 구현 migration 후보:

1. `workflow_runs.app_id`
2. `workflow_runs.correlation_id`
3. `workflow_runs.request_id`
4. `workflow_runs.workflow_task_id`
5. `workflow_runs.trace_metadata`
6. `workflow_runs.redaction_applied`
7. `workflow_runs.pii_detected`
8. `workflow_runs.redaction_policy_id`
9. `workflow_runs.retention_policy_id`
10. `workflow_runs.visibility_policy_id`
11. `workflow_runs.payload_storage_mode`
12. `workflow_node_runs.duration`
13. `workflow_node_runs.trace_metadata`
14. `workflow_node_runs.redaction_applied`
15. `workflow_node_runs.pii_detected`
16. `workflow_node_runs.redaction_policy_id`
17. `workflow_node_runs.parent_node_run_id`
18. `workflow_node_runs.sequence`
19. `workflow_node_runs.retry_count`
20. `trace_redaction_policies`
21. `trace_retention_policies`
22. `trace_visibility_policies`
23. `trace_payloads`
24. `trace_payload_access_events`

## 호환성 고려

- 기존 API 응답 schema가 깨지지 않아야 한다.
- 기존 로그 레코드는 신규 필드가 null이어도 조회 가능해야 한다.
- 과거 run의 app id는 fallback join으로 유도한다.
- migration downgrade가 가능해야 한다.

## 영향 파일 후보

- `apps/shared/db/models/workflow_run.py`
- `apps/shared/db/models/*policy*.py`
- `apps/shared/schemas/log.py`
- 신규 `apps/shared/schemas/tracing.py`
- `apps/shared/alembic/versions/*`
- `apps/log_system/tasks.py`
- `apps/workflow_engine/workflow/core/workflow_logger.py`
- `apps/gateway/api/v1/endpoints/workflow.py`
- 신규 `apps/gateway/api/v1/endpoints/tracing.py`
