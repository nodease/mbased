# 1차 전체 구현 기본 정책 결정

## 목적

Tracing 구현 중 흔들리면 안 되는 기본 정책을 고정한다.

## Trace/Span ID

- `WorkflowRun.id`는 domain trace id이다.
- `WorkflowNodeRun.id`는 domain span id이다.
- 별도 `trace_id` 컬럼은 추가하지 않는다.
- external distributed tracing과 연결이 필요하면 `correlation_id`, `request_id`, `workflow_task_id`를 사용한다.

## app_id

- 1차 전체 구현에서는 `workflow_runs.app_id`를 추가한다.
- 신규 run 생성 시 app id를 저장한다.
- 기존 데이터 또는 app id가 null인 경우 fallback join으로 app id를 유도한다.
- app owner 판별에 `workflow_runs.user_id`를 사용하지 않는다.

Fallback 경로:

```text
workflow_runs.workflow_id
  -> workflows.id
  -> workflows.app_id
  -> apps.id
```

배포 실행 보조 경로:

```text
workflow_runs.deployment_id
  -> workflow_deployments.id
  -> workflow_deployments.app_id
  -> apps.id
```

## Duration과 latency

- DB 컬럼 `duration`은 초 단위 float이다.
- 외부 호출 세부 latency는 `trace_metadata.latency_ms`에 millisecond 단위로 저장한다.
- `latency`, `retrieval latency`, `execution time`처럼 단위가 모호한 키를 새로 만들지 않는다.

## `process_data`와 `trace_metadata`

- `process_data`는 실행 시점 설정 스냅샷이다.
- `trace_metadata`는 실행 중 관측값이다.
- `trace_metadata`에는 원문성 payload를 저장하지 않는다.
- `trace_metadata`는 run/span/retention/gateway scope별 allowlist sanitizer를 통과한 값만 저장·반환한다.
- allowlist에 명시된 exact field는 유지하고, 임의 하위 key에는 민감 key/pattern denylist를 적용한다.

예:

- LLM temperature, top_p, max_tokens: `process_data`
- LLM token, cost, latency_ms: `trace_metadata`
- HTTP method, path 설정: `process_data`
- HTTP status_code, latency_ms: `trace_metadata`

## Payload 저장

- raw payload와 redacted payload는 분리 저장한다.
- redacted payload는 `trace_payloads.redacted_payload`에 저장한다.
- raw payload는 `trace_payloads.raw_payload_encrypted`에만 저장할 수 있다.
- raw payload 저장은 기본 비활성이다.
- secret 계열 값은 raw 저장 허용 정책이 있어도 저장하지 않는다.
- raw payload 암호화 실패 시 raw 저장으로 fallback하지 않고 redacted copy만 유지하며 전용 로그/메트릭을 남긴다.
- 기존 `workflow_runs.inputs`, `workflow_runs.outputs`, `workflow_node_runs.inputs`, `workflow_node_runs.outputs`는 기존 API 호환을 위해 유지하되 redacted copy만 저장한다.
- payload 저장은 기본 append-only이다.
- retry, streaming, partial output, 재실행 payload는 기존 row를 덮어쓰지 않고 새 row로 추가한다.
- 신규 trace API는 기본적으로 latest payload view를 반환한다.
- payload history가 필요하면 `sequence`, `attempt`, `created_at` 기준으로 조회한다.

## Redaction

- payload 저장 확장보다 Redaction service 구현이 선행되어야 한다.
- secret 계열 값은 정책과 무관하게 원문 저장 금지다.
- prompt/completion은 기본적으로 redaction 후 저장한다.
- PII는 기본적으로 redaction 후 저장한다.
- redaction 실패 시 raw 저장으로 fallback하지 않는다.
- redaction metadata에는 값이 아니라 field path, rule id, detector type만 저장한다.

## Access control

- app owner는 기본적으로 본인 app trace metadata를 조회할 수 있다.
- system admin 정책은 app owner 권한보다 우선한다.
- system admin은 app owner의 trace 조회를 차단할 수 있다.
- raw payload 조회 권한이 없으면 기본 응답은 403이다.
- 자동 redacted downgrade는 명시 정책이 있을 때만 허용한다.
- raw payload 조회 시도는 허용/차단 모두 기록한다.

## Policy scope

정책 우선순위:

1. app
2. organization
3. global

deny 정책은 allow 정책보다 우선한다.

## Policy table

1차 전체 구현에서는 다음 policy table을 추가한다.

- `trace_redaction_policies`
- `trace_retention_policies`
- `trace_visibility_policies`

정책값은 코드 상수로 고정하지 않는다. 단, DB에 정책 row가 없을 때 적용할 보수적 bootstrap default는 코드에 둘 수 있다.

## Policy bootstrap default

global policy row는 migration 또는 seed 단계에서 생성한다.

DB에 policy row가 없을 때만 다음 보수적 fallback을 적용한다.

- redaction enabled
- PII detection enabled
- raw payload storage disabled
- prompt/completion storage enabled with redaction
- owner trace metadata access enabled
- owner redacted payload access disabled by default
- owner raw payload access disabled
- system admin raw payload access disabled until encryption service is configured
- metadata retention 90 days
- redacted payload retention 30 days
- raw payload retention 7 days only when raw storage is enabled

fallback default는 운영 정책의 불변값이 아니라 bootstrap safety net이다.

## Retention

- retention policy 모델을 구현한다.
- 1차 구현의 retention purge 실행 경로는 Celery task와 system admin 수동 purge API로 고정한다.
- 자동 스케줄러 기반 purge는 후속 확장으로 둔다.
- raw payload, redacted payload, metadata, prompt/completion 보관 기간은 분리한다.
- purge는 idempotent해야 한다.

## API

- 신규 trace router를 1차 전체 구현 범위에 포함한다.
- 기존 workflow run API는 유지한다.
- 신규 trace router는 기존 workflow run API와 병행한다.
- policy 조회/수정 API도 1차 구현 범위에 포함한다.
- trace 목록 API는 권한 판정을 위해 제한된 범위만 scan하며, scan limit에 걸리면 기존 `total`을 유지하되 `total_is_estimated`, `has_more`, `scan_limit_reached`로 추정 여부를 알린다.
- metadata view의 `error_message`는 반환하지 않는다. redacted/raw view의 `error_message`도 원문 경로가 아니라 기존 UI 호환용 redacted field로만 취급한다.

## Existing API와 Trace API source of truth

- 기존 workflow run API는 기존 `inputs`, `outputs`, `node_runs` compatibility field를 반환한다.
- 신규 trace API는 `trace_payloads`를 payload source of truth로 사용한다.
- 기존 compatibility field와 `trace_payloads`가 불일치하면 신규 trace API에서는 `trace_payloads`를 우선한다.
- 기존 workflow run API에서 raw payload를 새로 노출하지 않는다.

## Policy API 권한

- global policy 조회/수정은 system admin만 가능하다.
- organization policy 조회/수정은 1차 구현에서는 system admin만 가능하다.
- app policy 조회/수정도 1차 구현에서는 system admin만 가능하다.
- app owner self-visibility 설정은 별도 요구가 확정될 때까지 허용하지 않는다.

## UI

- 신규 관리자 UI는 1차 tracing 구현 범위가 아니다.
- 기존 로그 UI를 깨지 않는다.
- API와 schema는 후속 UI가 조회할 수 있도록 설계한다.

## Guardrail

- 전역 AWS Bedrock Guardrail 결과는 AWS Bedrock에서 추적한다.
- 전역 Bedrock Guardrail 결과 payload는 내부 trace에 저장하지 않는다.
- Moduly Guardrail node는 일반 span으로 기록한다.
- Moduly Guardrail node의 decision, blocked, policy_id, rule_id, reason_code, reason_redacted, severity, latency_ms는 `trace_metadata.guardrail`에 저장한다.
- 차단 대상 원문과 민감한 차단 사유는 raw로 저장하지 않는다.
