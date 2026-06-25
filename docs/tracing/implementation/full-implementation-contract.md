# 1차 전체 구현 계약

## 목적

1차 전체 구현에서 반드시 지켜야 하는 계약을 정리한다.

## ID 계약

- `WorkflowRun.id`는 domain trace id이다.
- `WorkflowNodeRun.id`는 domain span id이다.
- 별도 `trace_id` 컬럼은 추가하지 않는다.
- system trace 연계는 `correlation_id`, `request_id`, `workflow_task_id`로 처리한다.

## 시간 단위 계약

- `duration` DB 컬럼은 초 단위 float이다.
- `latency_ms`는 `trace_metadata` 내부에서만 사용한다.
- 단위 없는 `latency` 신규 키를 만들지 않는다.

## 데이터 경계 계약

- `process_data`는 실행 시점 설정 스냅샷이다.
- `trace_metadata`는 실행 중 관측값이다.
- 설정값과 관측값을 같은 필드에 섞지 않는다.
- `trace_metadata`는 run/span/retention/gateway scope별 allowlist sanitizer를 통과해야 한다.
- 원문성 payload, error 원문, RAG chunk 본문은 `trace_metadata`에 저장하지 않는다.

## Payload 계약

- raw payload와 redacted payload를 분리 저장한다.
- redacted payload는 기본 저장 대상이다.
- raw payload는 암호화된 별도 필드에만 저장할 수 있다.
- raw payload 저장은 기본 비활성이다.
- secret 계열 값은 어떤 정책에서도 raw 저장하지 않는다.
- raw payload 암호화 실패 시 raw 저장으로 fallback하지 않는다.
- 기존 run/node `inputs`, `outputs` 호환 필드에는 redacted copy만 저장한다.
- payload 저장은 기본 append-only이다.
- trace API의 기본 payload view는 latest이다.
- history view는 `sequence`, `attempt`, `created_at` 기준으로 정렬한다.

## Redaction 계약

- Redaction service는 payload 저장 확장보다 먼저 구현한다.
- secret 계열 값은 정책과 무관하게 원문 저장 금지다.
- redaction 실패 시 raw 저장으로 fallback하지 않는다.
- prompt/completion은 기본적으로 redaction 후 저장한다.
- redaction metadata에는 값이 아니라 경로와 rule id만 저장한다.

## Access 계약

- app owner trace 조회는 system admin 정책으로 차단될 수 있다.
- raw view 권한이 없으면 기본 403이다.
- 자동 redacted downgrade는 명시 정책이 있을 때만 허용한다.
- raw payload 조회 시도는 허용/차단 모두 기록한다.
- RBAC 기반 상세 권한은 `TraceAccessService`와 `TraceRbacService` 경계로 격리한다.

## app_id 계약

- `WorkflowRun`에 `app_id`를 추가한다.
- 신규 run 생성 시 app id를 저장한다.
- 기존 run은 workflow/deployment join fallback을 지원한다.
- app owner 판별에 `WorkflowRun.user_id`를 사용하지 않는다.

## API 계약

- 신규 trace router를 구현한다.
- 기존 workflow run API를 깨지 않는다.
- 신규 필드는 optional이다.
- 기존 프론트 로그 UI가 깨지지 않아야 한다.
- policy 조회/수정 API를 구현한다.
- endpoint에 직접 정책 로직을 넣지 않는다.
- metadata view에서는 `error_message`, `inputs`, `outputs`, `process_data`를 노출하지 않는다.
- trace list API는 무제한 scan을 피하고 scan limit 도달 시 `total_is_estimated`, `has_more`, `scan_limit_reached`를 반환한다.

## Retention 계약

- retention policy 모델을 구현한다.
- Celery purge task를 구현한다.
- system admin 전용 수동 purge API를 구현한다.
- 자동 스케줄러 기반 purge는 후속 확장으로 둔다.
- raw payload, redacted payload, metadata 보관 기간은 분리한다.
- purge는 idempotent해야 한다.

## Guardrail 계약

- 전역 AWS Bedrock Guardrail 결과 payload는 내부 trace에 저장하지 않는다.
- Moduly Guardrail node는 일반 span으로 저장한다.
- 차단 사유는 redacted reason과 reason code까지만 저장한다.

## 테스트 계약

- 모델 변경에는 migration과 schema 테스트가 따라야 한다.
- redaction 관련 변경에는 secret 미노출 테스트가 포함되어야 한다.
- access control 변경에는 system admin, app owner, regular user 케이스가 포함되어야 한다.
- payload 저장 변경에는 raw/redacted 분리 테스트가 포함되어야 한다.
- trace router 변경에는 목록/상세/span/payload/policy API 테스트가 포함되어야 한다.
