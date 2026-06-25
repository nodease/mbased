# 1차 전체 구현 계획

## 목적

Tracing 확장을 구현할 때 따라야 할 단계와 영향 파일을 정의한다.

1차 구현은 최소 MVP가 아니라 backend tracing에 필요한 데이터 모델, 수집, redaction, payload 저장, 조회 API, policy 관리, retention 기반을 함께 구현한다.

## 공통 작업 규칙

- 구현 전 관련 문서를 읽는다.
- 영향 파일과 구현 계획을 먼저 검토한다.
- 애매한 정책 판단은 보수적이고 보안 우선인 방향으로 결정하고, PR 설명 또는 별도 설계 기록에 남긴다.
- `.env` 또는 secret 값을 출력하지 않는다.
- Korean text는 UTF-8로 보존한다.
- controller에 business logic을 넣지 않는다.
- unrelated file을 수정하지 않는다.
- migration은 모델 변경과 함께 작성한다.
- 테스트를 실행하고 결과를 보고한다.

## Phase 0: 사전 확인

목표:

- 작업 상태 확인
- 현재 로그 테스트와 API 구조 확인
- 데이터 모델 문서와 tracing 문서 확인

명령 후보:

```powershell
git branch --show-current
git status --short
rg --files docs
```

완료 기준:

- 작업 트리에 예상하지 못한 변경이 없다.
- 구현 기준 문서를 확인했다.

## Phase 1: 데이터 모델 및 migration

목표:

- trace/span/payload/policy/access event 저장 구조를 만든다.

확인 문서:

- `../../data-model/system-data-model-design.md`
- `requirements/tracing-requirements.md`
- `policies/policy-decisions.md`
- `implementation/full-implementation-contract.md`
- `design/data-model.md`

영향 파일 후보:

- `apps/shared/db/models/workflow_run.py`
- 신규 또는 기존 policy model 파일
- `apps/shared/schemas/log.py`
- 신규 `apps/shared/schemas/tracing.py`
- `apps/shared/alembic/versions/*`

작업:

- `workflow_runs.app_id` 추가
- `workflow_runs.correlation_id`, `request_id`, `workflow_task_id` 추가
- `workflow_runs.trace_metadata`, redaction/policy fields 추가
- `workflow_node_runs.duration`, `trace_metadata`, redaction fields 추가
- `trace_payloads` 추가
- `trace_payloads.sequence`, `trace_payloads.attempt` 추가
- `trace_payload_access_events` 추가
- `trace_redaction_policies` 추가
- `trace_retention_policies` 추가
- `trace_visibility_policies` 추가
- migration 작성
- downgrade 작성

## Phase 2: Redaction 및 policy resolution

목표:

- payload 저장 확장 전에 민감정보 마스킹 경로를 만든다.

작업:

- `TracePolicyService` 또는 동등한 policy resolution service 구현
- `TraceRedactionService` 구현
- secret detector 구현
- PII detector 기본 rule 구현
- policy scope 우선순위 적용
- redaction metadata 생성
- redaction 실패 시 raw 저장 방지

완료 기준:

- redaction service 없이 raw/redacted payload 저장 확장을 진행하지 않는다.

## Phase 3: Payload 저장 경로

목표:

- raw/redacted payload 분리 저장 구조를 workflow logging 경로에 연결한다.

작업:

- 기존 `inputs`, `outputs`에는 redacted compatibility copy 저장
- `trace_payloads.redacted_payload` 저장
- raw storage policy와 encryption path 확인
- raw storage 비활성 시 raw 저장 생략
- prompt/completion/http/stdout/stderr/retrieved_context/guardrail_reason payload kind 처리
- payload access event 기록 service 준비
- append-only payload 저장 적용
- latest payload view와 history view 구분

주의:

- Celery task payload에 raw plaintext를 넣지 않는다.
- secret field는 raw storage가 허용되어도 저장하지 않는다.

## Phase 4: Node type별 metadata 수집

목표:

- LLM/RAG/HTTP/Sandbox/Workflow/Moduly Guardrail 노드의 metadata 저장 형식을 표준화한다.

작업:

- `process_data`와 `trace_metadata` 경계 적용
- run/span/retention/gateway scope별 metadata allowlist sanitizer 적용
- LLM metadata 수집
- RAG metadata 수집
- HTTP metadata 수집
- Sandbox metadata 수집
- Workflow/Submodule metadata 수집
- Moduly Guardrail node metadata 수집
- `latency_ms` 단위 통일

주의:

- credential 원문 저장 금지
- retrieved chunk text는 metadata에 저장하지 않고 payload policy 대상
- Guardrail 차단 대상 원문 저장 금지

## Phase 5: Trace router 및 access control

목표:

- 신규 trace router와 권한 정책을 구현한다.

작업:

- `GET /api/v1/traces`
- `GET /api/v1/traces/{trace_id}`
- `GET /api/v1/traces/{trace_id}/spans`
- `GET /api/v1/traces/{trace_id}/payloads`
- `GET /api/v1/traces/{trace_id}/payloads/{payload_id}`
- `POST /api/v1/tracing/retention/purge`
- redaction/retention/visibility policy GET/PATCH API
- metadata/redacted/raw view 구분
- trace list bounded scan 및 `total_is_estimated`, `has_more`, `scan_limit_reached` 응답 필드
- app owner 판별 경로 구현
- system admin override 적용
- raw payload 조회 권한 분리
- raw view 권한 없음은 403 처리
- raw payload access event 기록

주의:

- 기존 workflow run API를 깨지 않는다.
- endpoint에 policy business logic을 넣지 않는다.

## Phase 6: Retention purge

목표:

- retention policy에 따라 만료 payload와 trace 데이터를 처리할 수 있게 한다.

작업:

- retention policy loading
- 만료 대상 계산
- raw/redacted/metadata별 만료 처리
- delete/anonymize/summarize action 구조
- Celery purge task 구현
- system admin 전용 수동 purge API 구현
- dry run 지원
- idempotency 처리

주의:

- 자동 스케줄러 기반 purge는 1차 구현 필수가 아니다.
- Celery beat 또는 APScheduler 연결은 후속 확장으로 둔다.

## Phase 7: 정리 및 문서화

작업:

- 공식 문서 업데이트
- PR 설명 작성
- 남은 미해결 질문 정리
