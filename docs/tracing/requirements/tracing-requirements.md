# Tracing 1차 전체 구현 요구사항

## 목적

이 문서는 Tracing 확장이 반드시 만족해야 하는 요구사항을 정의한다. 1차 구현은 최소 MVP가 아니라, 백엔드 실행 추적에 필요한 데이터 수집, 저장, 조회 API, 정책 모델을 가능한 한 완결된 형태로 구현하는 것을 목표로 한다.

## 범위

### 포함 범위

- 내부 DB 기반 백엔드 실행 추적
- 워크플로우 실행 단위 trace 기록
- 노드 실행 단위 span 기록
- 신규 trace router 구현
- trace 목록, 상세, span, payload 조회 API
- redaction, retention, visibility policy 모델 및 관리 API
- LLM, RAG, HTTP, Sandbox, Workflow, Moduly Guardrail 노드의 실행 metadata 기록
- 입력, 출력, prompt, completion, HTTP payload, stdout, stderr, retrieved context 저장 구조
- raw payload와 redacted copy의 분리 저장
- 저장 전 redaction 적용
- trace 조회 권한 정책
- raw payload 조회 이벤트 기록
- retention purge job 기반

### 제외 범위

- 브라우저 UI 상호작용 이벤트 추적
  - 노드 생성, 이동, 삭제
  - 엣지 연결 또는 해제
  - 사이드패널 값 수정
  - 탭 전환, 줌, 팬
- 신규 관리자 화면 구현
- OpenTelemetry, Jaeger, Tempo 도입
- RBAC 기반 상세 권한 통합
- Audit 전체 구현
- 전역 AWS Bedrock Guardrail 결과 내부 payload 저장

## 기능 요구사항

### FR-1. Trace 생성

워크플로우 실행이 시작되면 trace가 생성되어야 한다.

- trace id는 `WorkflowRun.id`를 사용한다.
- 별도 `trace_id` 컬럼은 추가하지 않는다.
- trace는 workflow, app, user, trigger, deployment 정보를 연결해야 한다.
- `workflow_runs.app_id`를 저장해야 한다.
- 과거 데이터 또는 예외 상황에서는 workflow/deployment join으로 app id를 유도할 수 있어야 한다.
- trace는 started, finished, failed, stopped 상태 변화를 기록해야 한다.

### FR-2. Span 생성

각 노드 실행은 span으로 기록되어야 한다.

- span id는 `WorkflowNodeRun.id`를 사용한다.
- span은 trace id와 연결되어야 한다.
- span은 node id, node type, status, started_at, finished_at을 기록해야 한다.
- span은 duration을 초 단위 float로 기록해야 한다.
- span은 실행 중 관측값을 `trace_metadata`에 기록해야 한다.
- `trace_metadata`는 scope별 allowlist sanitizer를 통과해야 하며 원문성 payload를 포함하면 안 된다.

### FR-3. Payload 저장

trace와 span은 입력과 출력을 저장할 수 있어야 한다.

- raw payload와 redacted payload를 같은 컬럼에 섞지 않는다.
- redacted payload는 `trace_payloads.redacted_payload`에 저장한다.
- raw payload는 정책으로 허용되고 암호화 경로가 준비된 경우에만 `trace_payloads.raw_payload_encrypted`에 저장한다.
- secret 계열 값은 정책과 무관하게 원문 저장 금지다.
- redaction 실패 시 raw 저장으로 fallback하지 않는다.
- payload 저장은 append-only를 기본으로 한다.
- trace API의 기본 payload 응답은 latest view이다.
- payload kind는 최소한 `input`, `output`, `prompt`, `completion`, `http_request`, `http_response`, `stdout`, `stderr`, `retrieved_context`, `guardrail_reason`을 지원한다.

### FR-4. LLM metadata

LLM 노드는 다음 metadata를 저장할 수 있어야 한다.

- provider
- model
- credential_id
- prompt payload reference
- completion payload reference
- prompt_tokens
- completion_tokens
- total_tokens
- total_cost
- latency_ms
- retry_count
- 주요 model parameter

### FR-5. RAG metadata

RAG 또는 knowledge base 연동은 다음 metadata를 저장할 수 있어야 한다.

- knowledge_base_id
- document_id
- filename
- page_number
- similarity_score
- latency_ms
- retrieved context payload reference

RAG 원문 chunk content, text, body, prompt, response는 metadata에 저장하지 않고 `retrieved_context` payload 경로로만 저장해야 한다.

### FR-6. HTTP/Sandbox metadata

HTTP 노드는 다음 metadata를 저장할 수 있어야 한다.

- method
- host
- path
- status_code
- latency_ms
- request_size
- response_size
- retry_count

Sandbox 또는 code 실행은 다음 metadata를 저장할 수 있어야 한다.

- execution_time_ms
- exit_code
- timeout
- stdout payload reference
- stderr payload reference

### FR-7. Moduly Guardrail node metadata

전역 AWS Bedrock Guardrail은 AWS Bedrock trace에서 추적하고 내부 trace payload에는 저장하지 않는다.

Moduly Guardrail node는 일반 span으로 기록해야 한다.

- decision
- blocked
- policy_id
- rule_id
- reason_code
- reason_redacted
- severity
- latency_ms

차단 대상 원문과 민감한 차단 사유는 raw로 저장하지 않는다.

### FR-8. Redaction

입력, 출력, prompt, completion, stdout, stderr, HTTP payload, retrieved context, guardrail reason은 저장 전 redaction 적용 대상이다.

- redaction 정책은 하드코딩하지 않는다.
- 정책은 시스템 관리자가 수정 적용할 수 있어야 한다.
- 정책 scope는 `global`, `organization`, `app`을 지원한다.
- app policy가 organization/global policy보다 우선한다.
- deny는 allow보다 우선한다.
- redaction 적용 여부와 PII/secret 탐지 여부를 metadata로 남겨야 한다.

### FR-9. Retention

trace 보관 기간과 삭제 정책은 하드코딩하지 않는다.

- metadata, redacted payload, raw payload, prompt/completion의 보관 기간을 분리할 수 있어야 한다.
- 실패 trace는 별도 보관 기간을 가질 수 있어야 한다.
- retention purge 실행 경로를 구현해야 한다.
- 1차 구현의 purge 실행 경로는 Celery task와 system admin 수동 purge API로 고정한다.
- 자동 스케줄러 기반 purge는 후속 확장으로 둔다.
- purge는 idempotent해야 한다.

### FR-10. Access control

trace 조회는 소유권만으로 결정하지 않는다.

- system admin은 전체 trace metadata를 조회할 수 있어야 한다.
- app owner는 기본적으로 본인 app trace metadata를 조회할 수 있다.
- system admin은 app owner의 trace 조회를 차단할 수 있어야 한다.
- raw payload 조회는 metadata/redacted 조회보다 강한 권한이 필요하다.
- 권한 부족 상태에서 raw view 요청은 기본적으로 403을 반환한다.
- raw payload 조회 시도는 허용/차단 여부와 함께 기록해야 한다.
- app owner 판별은 `workflow_runs.user_id`가 아니라 app 관계를 통해 수행해야 한다.

### FR-11. Trace API

신규 trace router를 구현해야 한다.

필수 API:

- `GET /api/v1/traces`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/traces/{trace_id}/spans`
- `GET /api/v1/traces/{trace_id}/payloads`
- `GET /api/v1/traces/{trace_id}/payloads/{payload_id}`
- `POST /api/v1/tracing/retention/purge`
- `GET /api/v1/tracing/policies/redaction`
- `PATCH /api/v1/tracing/policies/redaction`
- `GET /api/v1/tracing/policies/retention`
- `PATCH /api/v1/tracing/policies/retention`
- `GET /api/v1/tracing/policies/visibility`
- `PATCH /api/v1/tracing/policies/visibility`

기존 workflow run API는 깨지지 않아야 한다.

### FR-12. API 호환성

- 기존 workflow run API의 기존 필드는 유지한다.
- 신규 필드는 optional로 추가한다.
- 기존 프론트 로그 UI가 깨지지 않아야 한다.
- 신규 trace router는 기존 workflow run API를 대체하지 않고 병행한다.

## 비기능 요구사항

### NFR-1. 보안

- secret 원문은 trace, log, test output에 남기지 않는다.
- `.env` 내용은 출력하지 않는다.
- redaction 실패가 raw 저장으로 이어지면 안 된다.
- raw payload 저장은 암호화를 전제로 한다.
- raw payload 암호화 실패가 raw 저장 또는 평문 저장으로 이어지면 안 된다.
- raw payload 조회 시도는 기록한다.

### NFR-2. 호환성

- 기존 로그 데이터는 신규 필드가 없어도 조회 가능해야 한다.
- migration downgrade가 가능해야 한다.
- 과거 run의 app id는 fallback join으로 유도할 수 있어야 한다.

### NFR-3. 성능

- trace 저장은 workflow 실행 경로를 과도하게 지연시키지 않아야 한다.
- 기존 Celery log queue 구조를 우선 유지한다.
- trace 목록 조회를 위해 `workflow_runs.app_id`, `started_at`, `status` index를 고려한다.
- trace 목록 조회는 권한 판정 때문에 무제한 row scan을 수행하지 않아야 하며, scan limit 도달 시 응답에 추정 여부를 표시해야 한다.

### NFR-4. 확장성

- organization/team permission 기반 RBAC와 연결 가능해야 한다.
- system trace와 연결할 수 있도록 correlation id 확장 여지를 둔다.
- OpenTelemetry 연동은 후속이어도 domain trace id와 충돌하지 않아야 한다.

## 완료 기준

- 요구사항별 구현 또는 보류 사유가 PR에 정리된다.
- trace/span 모델이 코드와 문서에서 일관된다.
- 신규 trace router가 구현된다.
- raw/redacted payload 분리 저장 구조가 구현된다.
- secret 저장 금지 테스트 또는 검증이 포함된다.
- redaction, access control, retention 정책 모델이 구현된다.
- app owner 판별 경로가 `WorkflowRun -> Workflow/App` 관계와 일관된다.
