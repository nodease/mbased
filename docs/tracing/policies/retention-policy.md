# Retention 정책

## 목적

Trace 데이터의 보관 기간과 삭제 방식을 정의한다.

## 원칙

- trace metadata, redacted payload, raw payload의 보관 기간은 분리한다.
- prompt/completion은 별도 보관 기간을 가질 수 있어야 한다.
- 실패 trace는 디버깅을 위해 성공 trace보다 길게 보관할 수 있다.
- 삭제 방식은 delete, anonymize, summarize 중 정책으로 선택할 수 있어야 한다.
- purge는 idempotent해야 한다.
- retention 정책은 코드 상수로 고정하지 않는다.

## 정책 모델

테이블: `trace_retention_policies`

필수 필드:

- `scope_type`
- `scope_id`
- `metadata_retention_days`
- `raw_payload_retention_days`
- `redacted_payload_retention_days`
- `prompt_completion_retention_days`
- `failed_trace_retention_days`
- `retention_action`
- `is_active`
- `updated_by`

`retention_action` 후보:

- `delete`
- `anonymize`
- `summarize`

정책 우선순위:

1. app
2. organization
3. global

## Bootstrap default 후보

DB에 정책 row가 없을 때만 적용하는 보수적 기본값이다. 시스템 관리자가 정책을 만들면 즉시 override되어야 한다.

- trace metadata: 90일
- redacted payload: 30일
- raw payload: 7일
- prompt/completion: 30일
- 실패 trace metadata: 90일
- 비용/토큰 집계: 180일 이상

이 값들은 운영 정책의 초기값일 뿐이며, 코드 상수로 고정된 불변 정책이 아니다.

## 삭제 대상 분리

Metadata:

- status
- trigger_mode
- started_at
- finished_at
- duration
- total_tokens
- total_cost
- node_type
- error category
- trace_metadata 중 민감하지 않은 집계 정보

Redacted payload:

- redacted inputs
- redacted outputs
- redacted prompt
- redacted completion
- redacted stdout/stderr

Raw payload:

- encrypted raw inputs
- encrypted raw outputs
- encrypted raw prompt
- encrypted raw completion
- encrypted retrieved chunk text
- encrypted stdout/stderr

## Purge job

1차 전체 구현에서 retention purge 실행 경로를 구현한다.

고정 정책:

- Celery task를 구현한다.
- system admin 전용 수동 purge API를 구현한다.
- 자동 스케줄러 기반 purge는 후속 확장으로 둔다.

후속 후보:

- Celery beat
- APScheduler
- 관리자 UI에서 수동 purge 실행

## Purge 처리 단계

```text
1. 활성 retention policy 조회
2. 만료 대상 trace/span/payload 조회
3. retention_action에 따라 delete/anonymize/summarize 수행
4. trace_payloads.retention_expires_at 기준으로 payload purge
5. 처리 결과 로그 기록
6. 실패 시 retry 또는 관리자 알림
```

## 수동 purge API

Endpoint:

- `POST /api/v1/tracing/retention/purge`

권한:

- system admin만 실행 가능

동작:

- 요청을 검증한다.
- dry run이면 대상 건수만 계산한다.
- 실제 실행이면 Celery purge task를 enqueue한다.
- purge task id를 반환한다.

자동 스케줄:

- 1차 구현에서는 자동 스케줄러를 필수로 두지 않는다.
- 자동화는 Celery beat 또는 운영 스케줄러 도입 시 후속으로 연결한다.

## Anonymize 후보

- `workflow_runs.inputs`를 `{ "purged": true }`로 대체
- `workflow_runs.outputs`를 `{ "purged": true }`로 대체
- `workflow_node_runs.inputs`를 `{ "purged": true }`로 대체
- `workflow_node_runs.outputs`를 `{ "purged": true }`로 대체
- `trace_payloads.redacted_payload`를 `{ "purged": true }`로 대체
- `trace_payloads.raw_payload_encrypted`를 null로 변경
- prompt/completion 제거
- error_message에서 민감정보 제거
- metadata는 정책에 따라 유지
- `trace_metadata.retention.purged_at` 기록

## 테스트 기준

- 보관 기간이 지난 raw payload가 삭제 또는 익명화된다.
- redacted payload와 metadata는 각자 정책에 따라 처리된다.
- 실패 trace는 별도 보관 기간을 적용받는다.
- 정책 값 변경 시 다음 purge 실행부터 반영된다.
- purge 작업은 이미 purge된 레코드에 대해 idempotent하다.
