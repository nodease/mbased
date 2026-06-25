# Trace 아키텍처

## 목적

Tracing 확장의 전체 구조와 용어를 정의한다.

## 핵심 모델

- `WorkflowRun = domain trace`
- `WorkflowNodeRun = domain span`

1차 전체 구현에서 `WorkflowRun.id`는 domain trace id이고, `WorkflowNodeRun.id`는 domain span id이다.

## Trace

하나의 워크플로우 실행 전체를 의미한다.

포함 정보:

- workflow id
- app id
- user id
- deployment id
- trigger mode
- started_at
- finished_at
- duration
- status
- inputs
- outputs
- error
- aggregate token/cost
- trace-level metadata

## Span

워크플로우 내부의 개별 노드 실행을 의미한다.

포함 정보:

- span id
- trace id
- node id
- node type
- status
- started_at
- finished_at
- duration
- inputs
- outputs
- error
- process_data
- trace_metadata

## Payload

trace/span의 입력, 출력, prompt, completion, HTTP payload, stdout, stderr, retrieved context, guardrail reason을 의미한다.

1차 전체 구현에서는 payload를 trace/span 본문 컬럼에 직접 섞지 않고 `trace_payloads`에 분리 저장한다.

저장 원칙:

- 기존 `inputs`와 `outputs` compatibility field에는 redacted copy만 저장한다.
- `trace_payloads.redacted_payload`는 기본 저장 대상이다.
- `trace_payloads.raw_payload_encrypted`는 policy가 허용하고 encryption path가 준비된 경우에만 저장한다.
- secret 계열 값은 raw payload에도 저장하지 않는다.
- payload는 append-only로 저장한다.
- API 기본 응답은 latest payload view를 사용한다.

## Metadata

`trace_metadata`는 payload 저장소가 아니라 검색, 분류, 요약을 위한 관측 metadata다.

저장 원칙:

- run/span/retention/gateway scope별 allowlist sanitizer를 적용한다.
- 원문성 입력, 출력, prompt, completion, HTTP body, RAG chunk 본문, error 원문은 저장하지 않는다.
- payload 본문이 필요한 경우 `trace_payloads`에 저장하고 redaction, access control, retention 정책을 적용한다.
- metadata view에서는 `inputs`, `outputs`, `process_data`, `error_message`를 반환하지 않는다.

## Workflow trace와 system trace

Workflow trace는 Moduly 도메인의 실행 흐름을 추적한다.

System trace는 Gateway, DB, Redis, Celery, Worker, 외부 API 호출까지 포함하는 일반적인 distributed tracing이다.

1차 전체 구현은 workflow trace를 구현한다. 다만 추후 system trace와 연결할 수 있도록 다음 값을 metadata로 확장할 수 있게 둔다.

- `correlation_id`
- `request_id`
- `workflow_task_id`
- `node_task_id`

## Audit과의 차이

Tracing은 실행 내부에서 무슨 일이 발생했는지 기록한다.

Audit은 누가 언제 무엇을 생성, 수정, 삭제, 조회, 실행했는지 기록한다.

Tracing은 Audit 시스템을 대체하지 않는다. 다만 raw payload 조회 시도는 tracing 내부 access event로 최소 기록하고, 필요하면 `audit_logs`와 연결해 함께 조회한다.

## 프론트엔드 상호작용 제외

1차 tracing 구현은 브라우저 UI 이벤트를 추적하지 않는다.

제외 예:

- 노드 생성, 이동, 삭제
- 엣지 연결 또는 해제
- 사이드패널 값 수정
- 탭 전환, 줌, 팬

## 관리자 UI 제외

1차 tracing 구현은 신규 관리자 화면을 구현하지 않는다. 다만 신규 trace router와 policy API를 구현해 후속 화면에서 표시할 수 있도록 준비한다.

## 구현 원칙

- 기존 실행 로그 구조를 우선 재사용한다.
- trace 저장은 비동기 log queue 경로를 유지한다.
- raw payload와 redacted payload는 분리 저장한다.
- raw payload 저장은 redaction policy와 encryption path를 거친다.
- secret 계열 값은 정책과 무관하게 원문 저장 금지다.
- app owner의 trace 조회는 system admin 정책으로 제한될 수 있다.
