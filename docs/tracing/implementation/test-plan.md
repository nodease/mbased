# 테스트 계획

## 목적

Tracing 1차 전체 구현의 안정성을 확인하기 위한 테스트 범위와 케이스를 정의한다.

## 테스트 원칙

- 작은 단위부터 검증한다.
- DB 모델 변경은 migration과 schema를 함께 검증한다.
- redaction은 실패하면 보안 사고로 이어질 수 있으므로 우선순위를 높게 둔다.
- 권한 테스트는 metadata/redacted/raw view를 구분한다.
- secret 값은 테스트 출력에 남기지 않는다.
- 신규 trace router는 기존 workflow run API 호환성과 함께 검증한다.

## 테스트 계층

### Unit test

- redaction service
- secret detector
- retention policy 계산
- trace metadata builder
- scope별 trace metadata sanitizer
- payload storage decision helper
- access decision helper
- app owner resolver

### Service test

- WorkflowLogger payload 생성
- LogSystem task 저장
- Trace query service
- Trace payload service
- policy resolution service
- payload access event 기록
- retention purge service

### API test

- workflow run detail 기존 호환
- trace list
- trace detail
- trace spans
- trace payloads
- redaction policy API
- retention policy API
- retention purge API
- visibility policy API
- 권한별 응답 차이

### Integration test

- 간단한 workflow 실행
- node span 생성/완료
- 실패 node span 기록
- LLM metadata 저장
- RAG metadata 저장
- HTTP metadata 저장
- Sandbox metadata 저장
- Guardrail node metadata 저장
- payload redaction 및 저장

## Phase별 테스트

### Phase 1: 데이터 모델 및 migration

- migration upgrade가 성공한다.
- migration downgrade가 성공한다.
- `workflow_runs.app_id`가 nullable로 추가된다.
- `workflow_node_runs.duration`은 초 단위 float이다.
- `trace_payloads`가 redacted/raw 분리 필드를 가진다.
- `trace_payloads`가 `sequence`, `attempt`를 가진다.
- policy table이 scope index를 가진다.
- 기존 run detail API가 신규 필드 null 상태에서도 깨지지 않는다.

### Phase 2: Redaction 및 policy resolution

- 이메일이 마스킹된다.
- 전화번호가 마스킹된다.
- Authorization header가 마스킹된다.
- Cookie header가 마스킹된다.
- API key로 보이는 값이 마스킹된다.
- nested JSON path가 마스킹된다.
- prompt/completion에 redaction이 적용된다.
- redaction 실패 시 raw payload가 저장되지 않는다.
- 정책이 꺼진 경우에도 secret 계열은 저장되지 않는다.
- app policy가 organization/global policy보다 우선한다.
- deny가 allow보다 우선한다.

### Phase 3: Payload 저장 경로

- redacted payload가 `trace_payloads.redacted_payload`에 저장된다.
- raw storage 비활성 시 `raw_payload_encrypted`는 null이다.
- raw storage 활성 시 plaintext raw가 저장되지 않는다.
- secret field는 raw storage 활성 상태에서도 raw에 포함되지 않는다.
- 기존 `inputs`/`outputs`에는 redacted compatibility copy만 저장된다.
- 같은 trace/span/payload_kind에 새 payload가 들어오면 기존 row를 덮어쓰지 않고 새 row가 추가된다.
- trace payload API의 기본 응답은 latest payload이다.
- history 조회 시 `sequence`, `attempt`, `created_at` 기준으로 정렬된다.
- payload kind별 저장이 동작한다.

### Phase 4: Node type별 metadata

- LLM node metadata에 provider/model/token/cost/latency_ms가 저장된다.
- RAG metadata에 knowledge base, document, filename, page, score, latency_ms 요약 정보가 저장된다.
- RAG metadata에 chunk content, text, body, prompt, response 원문이 저장되지 않는다.
- HTTP metadata에 method/status_code/latency_ms가 저장된다.
- Sandbox metadata에 exit code/stdout/stderr/timeout이 저장된다.
- Workflow/Submodule metadata에 child workflow/run id가 저장된다.
- Moduly Guardrail node metadata에 decision/block reason code가 저장된다.
- credential 원문은 저장되지 않는다.
- allowlist exact field인 `response_size`, `content_type`은 pattern denylist 때문에 제거되지 않는다.
- metadata view에서 run `error_message`와 span `process_data`가 반환되지 않는다.

### Phase 5: Trace router 및 access control

- trace list API가 동작한다.
- trace list API가 scan limit에 도달하면 `total_is_estimated`, `has_more`, `scan_limit_reached`를 반환한다.
- trace detail API가 동작한다.
- trace spans API가 동작한다.
- trace payloads API가 동작한다.
- system admin은 trace metadata를 조회할 수 있다.
- app owner는 본인 app trace metadata를 조회할 수 있다.
- system admin 정책으로 app owner 조회를 차단할 수 있다.
- app owner 판별은 `workflow_runs.user_id`가 아니라 app 관계를 사용한다.
- app owner raw payload 조회가 차단된다.
- raw view 요청 시 권한이 없으면 403이다.
- regular user는 trace를 조회할 수 없다.
- raw payload 조회 시도가 access event로 기록된다.
- raw payload 암호화 실패 시 raw 저장 없이 로그/메트릭이 기록된다.

### Phase 6: Retention purge

- retention policy 값이 로딩된다.
- 만료 대상 계산이 가능하다.
- raw payload 만료 처리가 가능하다.
- redacted payload 만료 처리가 가능하다.
- metadata 만료 처리가 가능하다.
- purge 작업은 idempotent하다.
- 이미 purge된 레코드는 중복 처리해도 깨지지 않는다.
- system admin만 수동 purge API를 실행할 수 있다.
- dry run은 실제 데이터를 변경하지 않는다.

## 실행 명령 후보

프로젝트 스크립트:

```bash
./scripts/test.sh
```

Gateway 테스트:

```bash
cd apps/gateway
.venv/bin/python -m pytest tests
```

Workflow Engine 테스트:

```bash
cd apps/workflow_engine
.venv/bin/python -m pytest tests
```

Shared 테스트:

```bash
apps/workflow_engine/.venv/bin/python -m pytest apps/shared/tests
```

Windows에서는 가상환경 경로가 `.venv/Scripts/python`일 수 있다.

## 완료 기준

- 변경한 계층에 맞는 테스트가 추가되거나 기존 테스트가 갱신된다.
- redaction 관련 테스트는 최소 1개 이상 포함된다.
- 권한 정책을 건드린 경우 system admin/app owner/regular user 케이스가 포함된다.
- payload 저장 변경에는 raw/redacted 분리 테스트가 포함된다.
- trace router 변경에는 API 테스트가 포함된다.
- 실행하지 못한 테스트는 이유와 대체 검증을 기록한다.
