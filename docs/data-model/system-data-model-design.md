# 전체 시스템 데이터 모델 설계

## 목적

이 문서는 현재 Moduly 데이터 모델을 정리하고, Tracing 확장에 필요한 신규 스키마를 통합 설계한다.

이 문서는 구현 시 다음 판단의 기준이 된다.

- 기존 테이블 간 관계
- app owner 판별 경로
- trace/span 저장 구조
- raw/redacted payload 분리 저장 구조
- redaction, retention, visibility policy 저장 구조
- 신규 trace router 구현에 필요한 조회 최적화 필드

## 설계 원칙

- 기존 모델을 우선 활용한다.
- 실행 추적은 `workflow_runs`와 `workflow_node_runs`를 중심으로 확장한다.
- payload 원문과 redacted copy를 같은 컬럼에 섞지 않는다.
- secret 원문은 어떤 저장소에도 저장하지 않는다.
- raw payload 저장은 기본 비활성이다.
- raw payload 저장을 허용하더라도 암호화된 별도 저장소를 사용한다.
- 정책은 코드 상수로 고정하지 않고 DB 기반 설정으로 관리한다.
- app owner 판별은 `workflow_runs.user_id`가 아니라 app 관계를 통해 수행한다.

## 현재 핵심 모델 요약

### `apps`

사용자가 만든 app의 최상위 단위다.

주요 필드:

- `id`
- `organization_id`
- `name`
- `description`
- `workflow_id`
- `active_deployment_id`
- `url_slug`
- `auth_secret`
- `is_api_enabled`
- `api_req_per_minute`
- `api_req_per_hour`
- `is_market`
- `forked_from`
- `created_by`
- `created_at`
- `updated_at`

Tracing 관점:

- `apps.created_by`는 기본 app owner 판별 기준이다.
- `auth_secret`은 절대 trace payload에 저장하면 안 된다.
- app 단위 trace visibility, redaction, retention policy scope의 기준이 된다.

### `workflows`

사용자가 편집하는 workflow 원본 또는 작업본이다.

주요 필드:

- `id`
- `app_id`
- `created_by`
- `organization_id`
- `version`
- `graph`
- `features`
- `hash`
- `env_variables`
- `runtime_variables`
- `created_at`
- `updated_at`

Tracing 관점:

- `workflows.app_id`는 trace가 어느 app에 속하는지 판별하는 핵심 경로다.
- `graph`의 node data는 `process_data` 스냅샷의 원천이다.
- `env_variables`와 secret성 runtime value는 trace에 raw로 저장하지 않는다.

### `workflow_deployments`

배포된 workflow 버전을 저장한다.

주요 필드:

- `id`
- `app_id`
- `version`
- `type`
- `graph_snapshot`
- `config`
- `input_schema`
- `output_schema`
- `description`
- `created_by`
- `created_at`
- `is_active`

Tracing 관점:

- 배포 실행의 app owner 판별 보조 경로다.
- `graph_snapshot`은 실행 시점 node 설정을 복원하는 기준이다.
- trace에는 `deployment_id`, `workflow_version`, `deployment_type` metadata를 남긴다.

### `workflow_runs`

워크플로우 전체 실행 1회를 저장한다.

현재 주요 필드:

- `id`
- `workflow_id`
- `user_id`
- `deployment_id`
- `workflow_version`
- `status`
- `trigger_mode`
- `inputs`
- `outputs`
- `error_message`
- `started_at`
- `finished_at`
- `duration`
- `meta_info`
- `total_tokens`
- `total_cost`

Tracing 관점:

- `workflow_runs.id`는 domain trace id다.
- `workflow_runs.user_id`는 실행 주체이며 app owner가 아니다.
- 1차 전체 구현에서는 조회 성능과 권한 단순화를 위해 `app_id`를 denormalized column으로 추가한다.
- trace-level metadata는 `meta_info`에만 계속 누적하지 않고 전용 필드와 payload 테이블로 분리한다.

### `workflow_node_runs`

워크플로우 내부 노드 실행 1회를 저장한다.

현재 주요 필드:

- `id`
- `workflow_run_id`
- `node_id`
- `node_type`
- `status`
- `inputs`
- `process_data`
- `outputs`
- `error_message`
- `started_at`
- `finished_at`

Tracing 관점:

- `workflow_node_runs.id`는 domain span id다.
- 1차 전체 구현에서는 span duration, metadata, redaction metadata를 명시 필드로 추가한다.
- `process_data`는 실행 시점 설정 스냅샷이다.
- `trace_metadata`는 실행 중 관측값이다.

### `llm_usage_logs`

LLM 호출 기록과 사용량/비용을 저장한다.

주요 필드:

- `id`
- `user_id`
- `organization_id`
- `credential_id`
- `model_id`
- `workflow_id`
- `workflow_run_id`
- `node_id`
- `prompt_tokens`
- `completion_tokens`
- `total_cost`
- `atency_ms`
- `status`
- `error_message`
- `created_at`

Tracing 관점:

- `workflow_run_id`와 `node_id`를 통해 trace/span과 연결할 수 있다.
- `atency_ms`는 코드상 오타로 보이며, 신규 tracing metadata에서는 `latency_ms`를 사용한다.
- prompt/completion 원문은 usage log가 아니라 trace payload 정책을 통해 별도 저장한다.

### `llm_credentials`

LLM API key와 인증 정보를 저장한다.

Tracing 관점:

- `encrypted_config` 안의 값은 trace에 저장하지 않는다.
- credential id는 필요 시 metadata로 남길 수 있으나 credential value는 절대 저장하지 않는다.

### `knowledge_bases`, `documents`, `document_chunks`

RAG 데이터 저장소다.

Tracing 관점:

- RAG span metadata는 `knowledge_base_id`, `document_id`, `filename`, `page_number`, `similarity_score`, `latency_ms`, `retrieved_context_payload_id` 같은 source summary만 기록한다.
- retrieved chunk text 원문은 metadata에 복사하지 않고 `retrieved_context` payload 정책 대상이다.
- `document_chunks.metadata.original_data`에는 민감 데이터가 있을 수 있으므로 trace로 복사할 때 redaction을 거친다.

### `organization`, `team_permission`, `user_team_permissions`, `workflow_team_permissions`

조직과 팀 권한을 표현하는 권한 모델이다.

주요 구조:

- `organization`: 조직 계층과 관리자를 저장한다. `parent_id`로 상위 조직을 연결할 수 있다.
- `users.organization_id`: 사용자의 기본 조직을 저장한다.
- `apps.organization_id`: app이 속한 조직을 저장한다.
- `workflows.organization_id`: workflow가 속한 조직을 저장한다.
- `llm_credentials.organization_id`: credential이 속한 조직을 저장한다.
- `llm_usage_logs.organization_id`: LLM 사용 로그가 속한 조직을 저장한다.
- `team_permission`: 조직 안에서 사용할 권한 묶음을 저장한다.
- `team_permission.auth_state`: `none`, `read`, `write`, `execute`, `admin` 중 하나의 권한 수준을 저장한다.
- `user_team_permissions`: 사용자와 조직 내 팀 권한을 연결한다.
- `workflow_team_permissions`: workflow와 조직 내 팀 권한을 연결한다.

Tracing 관점:

- policy scope는 `global`, `organization`, `app`을 지원한다.
- organization scope policy는 `organization_id`를 기준으로 해석한다.
- RBAC 모델은 존재하지만 Tracing 1차 구현의 기본 app owner 판별은 `apps.created_by`를 사용한다.
- team permission 기반 공동 소유권과 세부 payload 권한 연동은 `TraceRbacService` 경계에서 후속 통합한다.

## Audit 모델

상세 구현 방식은 `../audit_system.md`를 기준으로 한다. 이 문서에서는 전체 데이터 모델 관점에서 핵심 테이블과 Tracing과의 관계만 정리한다.

### `audit_logs`

사용자 행동과 데이터 변경 이력을 append-only로 저장하는 전체 감사 로그 테이블이다.

감사 로그는 두 계층으로 구분한다.

- `action`: 엔드포인트나 서비스에서 명시적으로 기록하는 사용자 행동
- `data_change`: ORM 변경 이력 리스너가 commit 이후 기록하는 데이터 변경 전/후

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | 감사 이벤트 id |
| `occurred_at` | `DateTime`, index | 이벤트 발생 시각 |
| `actor_id` | `UUID`, FK nullable, index | 행위 사용자. 사용자 삭제 시 `SET NULL`로 로그 보존 |
| `actor_type` | enum | `user`, `admin`, `system` |
| `category` | enum, index | `action`, `data_change` |
| `action` | `String(100)`, index | `workflow.deploy`, `connection.updated` 같은 행동 식별자 |
| `target_type` | `String(100)`, nullable | 대상 리소스 종류 |
| `target_id` | `String(255)`, nullable | 대상 리소스 id |
| `before` | `JSONB`, nullable | 변경 전 값. `data_change` 감사에서 사용 |
| `after` | `JSONB`, nullable | 변경 후 값. `data_change` 감사에서 사용 |
| `status` | enum | `success`, `failure` |
| `audit_metadata` | `JSONB`, nullable | request id, ip, user agent, actor snapshot 등 부가 정보 |

정책:

- `audit_logs`는 생성만 하고 수정/삭제하지 않는 append-only 모델로 운영한다.
- `actor_id`는 DB FK로 연결하되 사용자 삭제 후에도 감사 row는 보존한다.
- 사용자가 삭제되어도 행위자를 추적할 수 있도록 `audit_metadata.actor`에 id, email, name 등 actor snapshot을 저장한다.
- SQLAlchemy 예약어와 충돌하지 않도록 metadata 컬럼명은 `audit_metadata`를 사용한다.
- 민감 필드는 감사 로그에 평문으로 저장하지 않고 마스킹된 변경 표시만 남긴다.
- nested transaction(savepoint) 단위 감사는 보수적으로 제외한다.

### Audit과 Tracing의 관계

- `audit_logs`는 누가 언제 어떤 행동 또는 데이터 변경을 수행했는지 기록한다.
- Tracing은 워크플로우 실행 내부에서 어떤 일이 발생했는지 기록한다.
- `trace_payload_access_events`는 raw trace payload 조회 시도에 한정된 tracing 내부 안전장치다.
- 장기적으로 raw payload 조회 이벤트는 `audit_logs`와 연결하거나 통합 조회할 수 있다.
- 그 전까지 `trace_payload_access_events`는 raw payload 접근 허용/차단 기록을 별도로 보존한다.

## App Owner 판별 계약

trace 조회 권한에서 app owner를 판별할 때 `workflow_runs.user_id`를 사용하지 않는다.

기본 판별 경로:

```text
workflow_runs.app_id
  -> apps.id
  -> apps.created_by
```

`workflow_runs.app_id`가 없는 과거 데이터의 fallback 경로:

```text
workflow_runs.workflow_id
  -> workflows.id
  -> workflows.app_id
  -> apps.id
  -> apps.created_by
```

배포 실행의 보조 검증 경로:

```text
workflow_runs.deployment_id
  -> workflow_deployments.id
  -> workflow_deployments.app_id
  -> apps.id
```

정책:

- `workflow_runs.user_id`는 실행자 식별에만 사용한다.
- `apps.created_by`는 1차 구현의 기본 app owner다.
- organization/team permission 기반 공동 소유권은 RBAC 연동 단계에서 `TraceRbacService`를 통해 추가한다.
- system admin deny 정책은 app owner 권한보다 우선한다.

## Tracing 확장 모델

### `workflow_runs` 추가 필드

1차 전체 구현에서 추가한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `app_id` | `UUID`, FK nullable, index | trace가 속한 app. 신규 run부터 저장하고 기존 run은 fallback join 사용 |
| `correlation_id` | `String`, nullable, index | 외부 요청, worker, system trace 연계용 correlation id |
| `request_id` | `String`, nullable, index | Gateway request id 또는 외부 호출 request id |
| `workflow_task_id` | `String`, nullable | Celery workflow task id |
| `trace_metadata` | `JSONB`, nullable | trace-level 관측 metadata |
| `redaction_applied` | `Boolean`, default false | trace-level payload redaction 적용 여부 |
| `pii_detected` | `Boolean`, default false | PII 탐지 여부 |
| `redaction_policy_id` | `UUID`, nullable | 적용된 redaction policy |
| `retention_policy_id` | `UUID`, nullable | 적용된 retention policy |
| `visibility_policy_id` | `UUID`, nullable | 적용된 visibility policy |
| `payload_storage_mode` | `String`, default `redacted_only` | `none`, `redacted_only`, `raw_and_redacted` |

### `workflow_node_runs` 추가 필드

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `duration` | `Float`, nullable | span 실행 시간. 초 단위 |
| `trace_metadata` | `JSONB`, nullable | span-level 관측 metadata |
| `redaction_applied` | `Boolean`, default false | span payload redaction 적용 여부 |
| `pii_detected` | `Boolean`, default false | PII 탐지 여부 |
| `redaction_policy_id` | `UUID`, nullable | 적용된 redaction policy |
| `parent_node_run_id` | `UUID`, nullable | nested workflow 또는 submodule span 연결 |
| `sequence` | `Integer`, nullable | 같은 trace 안에서 관측된 실행 순서 |
| `retry_count` | `Integer`, default 0 | 노드 재시도 횟수 |

`trace_metadata`는 run/span/retention/gateway scope별 allowlist sanitizer를 통과한 값만 저장한다. 원문성 입력, 출력, prompt, completion, HTTP body, RAG chunk 본문, error 원문은 `trace_metadata`에 저장하지 않는다.

## Payload 분리 저장 모델

### `trace_payloads`

trace/span payload를 redacted copy와 raw encrypted copy로 분리 저장한다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | payload id |
| `workflow_run_id` | `UUID`, FK, index | trace id |
| `workflow_node_run_id` | `UUID`, FK nullable, index | span payload면 span id |
| `scope` | `String`, index | `trace` 또는 `span` |
| `payload_kind` | `String`, index | `input`, `output`, `prompt`, `completion`, `http_request`, `http_response`, `stdout`, `stderr`, `retrieved_context`, `guardrail_reason` |
| `sequence` | `Integer`, nullable | 같은 trace/span 안에서 payload가 관측된 순서 |
| `attempt` | `Integer`, default 1 | retry 또는 재실행 attempt 번호 |
| `redacted_payload` | `JSONB`, nullable | redaction 적용 payload |
| `raw_payload_encrypted` | `Text`, nullable | 암호화된 raw payload. 기본 null |
| `redaction_applied` | `Boolean`, default false | redaction 적용 여부 |
| `pii_detected` | `Boolean`, default false | PII 탐지 여부 |
| `secret_detected` | `Boolean`, default false | secret 패턴 탐지 여부 |
| `redaction_metadata` | `JSONB`, nullable | rule id, field path 등 값이 아닌 metadata |
| `storage_mode` | `String` | `redacted_only`, `raw_and_redacted`, `metadata_only` |
| `retention_expires_at` | `DateTime`, nullable, index | payload 만료 시각 |
| `retention_purged_at` | `DateTime`, nullable, index | 비삭제 purge 처리 완료 시각 |
| `created_at` | `DateTime` | 생성 시각 |

정책:

- `redacted_payload`는 기본 저장 대상이다.
- `raw_payload_encrypted`는 system admin policy가 허용하고 encryption service가 준비된 경우에만 저장한다.
- raw payload 암호화 실패 시 raw payload는 저장하지 않고 redacted copy만 유지한다.
- secret은 raw 저장 허용 정책이 있어도 저장하지 않는다.
- `redaction_metadata`에는 값이 아니라 field path, rule id, detector type만 저장한다.
- payload 저장은 기본 append-only이다.
- retry, streaming, partial output, 재실행으로 payload가 여러 번 관측되면 기존 row를 덮어쓰지 않고 새 row를 추가한다.
- API는 기본적으로 latest payload view를 반환하고, 필요 시 history 조회를 제공한다.

### `trace_payload_access_events`

raw payload 조회 자체를 감사하기 위한 이벤트다. Audit 전체 시스템이 구현되기 전까지 tracing 내부의 최소 조회 기록으로 둔다.

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | 이벤트 id |
| `payload_id` | `UUID`, FK nullable | 조회된 payload. payload 삭제 시 null 유지 |
| `workflow_run_id` | `UUID`, FK, index | trace id |
| `actor_user_id` | `UUID`, FK nullable | 조회 사용자. 사용자 삭제 시 감사 row는 유지하고 null 처리 |
| `actor_user_ref` | `String`, nullable, index | secret 기반 HMAC 단방향 actor reference |
| `view_level` | `String` | `metadata`, `redacted`, `raw` |
| `allowed` | `Boolean` | 허용 여부 |
| `reason_code` | `String` | 허용/차단 사유 |
| `created_at` | `DateTime` | 이벤트 시각 |

정책:

- raw payload 조회 요청은 허용/차단 모두 기록한다.
- 이 테이블은 `audit_logs`의 대체물이 아니라 raw payload 조회에 특화된 bridge다.
- `actor_user_ref`는 `TRACE_AUDIT_ACTOR_REF_SECRET`이 있을 때만 생성하고, secret이 없으면 단순 hash로 fallback하지 않는다.

## Policy 모델

### `trace_redaction_policies`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | 정책 id |
| `scope_type` | `String`, index | `global`, `organization`, `app` |
| `scope_id` | `UUID`, nullable, index | scope 대상 id |
| `redaction_enabled` | `Boolean` | redaction 활성 여부 |
| `raw_payload_storage_enabled` | `Boolean` | raw payload 저장 허용 여부 |
| `prompt_completion_storage_enabled` | `Boolean` | prompt/completion 저장 허용 여부 |
| `pii_detection_enabled` | `Boolean` | PII 탐지 활성 여부 |
| `store_redacted_copy_only` | `Boolean` | redacted copy만 저장 |
| `sensitive_headers` | `JSONB` | header mask rule |
| `sensitive_json_paths` | `JSONB` | JSON path rule |
| `sensitive_keywords` | `JSONB` | keyword rule |
| `regex_rules` | `JSONB` | regex rule |
| `replacement` | `String` | 마스킹 대체 문자열 |
| `is_active` | `Boolean` | 활성 여부 |
| `updated_by` | `UUID`, FK | 수정자 |
| `created_at` | `DateTime` | 생성 시각 |
| `updated_at` | `DateTime` | 수정 시각 |

### `trace_retention_policies`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | 정책 id |
| `scope_type` | `String`, index | `global`, `organization`, `app` |
| `scope_id` | `UUID`, nullable, index | scope 대상 id |
| `metadata_retention_days` | `Integer` | metadata 보관 기간 |
| `raw_payload_retention_days` | `Integer` | raw payload 보관 기간 |
| `redacted_payload_retention_days` | `Integer` | redacted payload 보관 기간 |
| `prompt_completion_retention_days` | `Integer` | prompt/completion 보관 기간 |
| `failed_trace_retention_days` | `Integer` | 실패 trace 보관 기간 |
| `retention_action` | `String` | `delete`, `anonymize`, `summarize` |
| `is_active` | `Boolean` | 활성 여부 |
| `updated_by` | `UUID`, FK | 수정자 |
| `created_at` | `DateTime` | 생성 시각 |
| `updated_at` | `DateTime` | 수정 시각 |

### `trace_visibility_policies`

| 컬럼 | 타입 | 설명 |
| --- | --- | --- |
| `id` | `UUID`, PK | 정책 id |
| `scope_type` | `String`, index | `global`, `organization`, `app` |
| `scope_id` | `UUID`, nullable, index | scope 대상 id |
| `owner_trace_access_enabled` | `Boolean` | app owner metadata 조회 허용 |
| `owner_redacted_payload_access_enabled` | `Boolean` | app owner redacted payload 조회 허용 |
| `owner_raw_payload_access_enabled` | `Boolean` | app owner raw payload 조회 허용 |
| `owner_prompt_completion_access_enabled` | `Boolean` | app owner prompt/completion 조회 허용 |
| `admin_raw_payload_access_enabled` | `Boolean` | system admin raw payload 조회 허용 |
| `admin_prompt_completion_access_enabled` | `Boolean` | system admin prompt/completion 조회 허용 |
| `deny_owner_trace_access` | `Boolean` | app owner trace 조회 명시 차단 |
| `default_view_level` | `String` | `metadata`, `redacted`, `raw` |
| `is_active` | `Boolean` | 활성 여부 |
| `updated_by` | `UUID`, FK | 수정자 |
| `created_at` | `DateTime` | 생성 시각 |
| `updated_at` | `DateTime` | 수정 시각 |

정책 우선순위:

1. app
2. organization
3. global

deny 정책은 allow 정책보다 우선한다.

## Guardrail Node Trace 모델

전역 AWS Bedrock Guardrail 결과는 내부 trace payload로 저장하지 않는다.

Moduly Guardrail node는 일반 span으로 저장한다.

`workflow_node_runs.trace_metadata.guardrail` 예시:

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

정책:

- guardrail reason은 redacted된 설명만 저장한다.
- 차단 대상 원문은 `trace_payloads`에도 raw로 저장하지 않는다.
- Bedrock trace와 연결이 필요하면 `correlation_id`만 남긴다.

## Trace 조회 API를 위한 인덱스 후보

권장 index:

- `workflow_runs.app_id`
- `workflow_runs.workflow_id`
- `workflow_runs.user_id`
- `workflow_runs.deployment_id`
- `workflow_runs.status`
- `workflow_runs.started_at`
- `workflow_runs.correlation_id`
- `workflow_node_runs.workflow_run_id`
- `workflow_node_runs.node_id`
- `workflow_node_runs.node_type`
- `workflow_node_runs.status`
- `trace_payloads.workflow_run_id`
- `trace_payloads.workflow_node_run_id`
- `trace_payloads.payload_kind`
- `trace_payloads.sequence`
- `trace_payloads.attempt`
- `trace_payloads.retention_expires_at`
- policy tables: `(scope_type, scope_id, is_active)`

## Migration 우선순위

1. `workflow_runs` trace-level 확장 필드
2. `workflow_node_runs` span-level 확장 필드
3. `trace_redaction_policies`
4. `trace_retention_policies`
5. `trace_visibility_policies`
6. `trace_payloads`
7. `trace_payload_access_events`

## 후속 확장 후보

- Trace visibility policy와 organization/team permission 연동
- OpenTelemetry span id와 domain span id 매핑
- trace summary materialized view
- retention purge worker
- scheduled retention purge automation
- `audit_logs`와 trace payload access event 연계 조회
- encrypted raw payload key rotation
