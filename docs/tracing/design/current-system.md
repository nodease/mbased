# 현재 시스템 분석

## 목적

현재 코드베이스에서 워크플로우 실행 로그가 어떻게 생성, 전달, 저장, 조회되는지 정리한다.

## 주요 코드 위치

### 데이터 모델

- `apps/shared/db/models/workflow_run.py`
  - `WorkflowRun`: 워크플로우 실행 단위 로그
  - `WorkflowNodeRun`: 노드 실행 단위 로그
- `apps/shared/db/models/llm.py`
  - LLM provider, model, credential, usage log 관련 모델

### 로그 호출 어댑터

- `apps/workflow_engine/workflow/core/workflow_logger.py`
  - WorkflowEngine에서 log system으로 Celery task를 전송한다.
  - 실제 DB 저장은 담당하지 않는다.

### 워크플로우 실행

- `apps/workflow_engine/workflow/core/workflow_engine.py`
  - 그래프 검증
  - 시작 노드 탐색
  - gevent 기반 병렬 노드 실행
  - node start/finish/error 이벤트 발행
  - workflow finish/error 처리

### 로그 저장

- `apps/log_system/tasks.py`
  - `log.create_run`
  - `log.update_run_finish`
  - `log.update_run_error`
  - `log.create_node`
  - `log.update_node_finish`
  - `log.update_node_error`
  - Celery `log` 큐를 통해 비동기 DB 저장을 수행한다.

### API

- `apps/gateway/api/v1/endpoints/workflow.py`
  - 실행 목록 조회
  - 실행 상세 조회
  - 실행 통계 조회
  - workflow draft 저장/조회
  - workflow execute/stream 실행

## 현재 실행 흐름

```text
Gateway API
  -> Celery workflow task
  -> WorkflowEngine
  -> WorkflowLogger
  -> Celery log task
  -> LogSystem
  -> PostgreSQL
```

스트리밍 실행에서는 Redis pub/sub도 사용한다.

```text
WorkflowEngine
  -> publish_workflow_event
  -> Redis pub/sub
  -> Gateway stream endpoint
  -> Client
```

## 현재 모델 요약

### WorkflowRun

현재 저장 정보:

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

- trace 본문으로 사용 가능하다.
- `meta_info`가 trace-level metadata 저장 후보이다.
- `duration`, `total_tokens`, `total_cost`는 trace-level 집계에 적합하다.

### WorkflowNodeRun

현재 저장 정보:

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

- span 본문으로 사용 가능하다.
- `process_data`는 현재 노드 옵션 스냅샷 저장 용도로 사용된다.
- span-level `duration`, `trace_metadata`, `redaction_applied`, `pii_detected` 필드는 아직 없다.

## 현재 장점

- 워크플로우 실행과 노드 실행이 이미 분리되어 있다.
- 로그 저장이 Celery `log` 큐로 분리되어 있다.
- 실행 로그 API와 프론트 로그 UI가 이미 있다.
- LLM 사용량 로그가 별도로 존재한다.

## 현재 한계

- trace/span 용어와 스키마가 명확히 정의되어 있지 않다.
- node span duration이 명시 필드로 없다.
- 노드 유형별 metadata 구조가 표준화되어 있지 않다.
- inputs/outputs/prompt/completion 저장 전 redaction 경로가 명확하지 않다.
- retention policy가 없다.
- app owner와 system admin의 trace 조회 정책이 없다.
- Celery task id, request id, correlation id가 trace와 명확히 연결되어 있지 않다.

## 유지할 책임 경계

- Gateway endpoint는 요청 처리와 권한 확인을 담당한다.
- Service 계층은 비즈니스 로직과 정책 적용을 담당한다.
- WorkflowEngine은 노드 실행과 실행 이벤트 생성을 담당한다.
- WorkflowLogger는 log task 전송을 담당한다.
- LogSystem은 DB 저장과 idempotent update를 담당한다.
- Shared model/schema는 서비스 간 공통 계약을 담당한다.

## 구현 지침

- `workflow.py` endpoint에 복잡한 tracing business logic을 직접 넣지 않는다.
- 저장 로직을 WorkflowEngine에 직접 몰아넣지 않는다.
- 기존 `WorkflowLogger`와 `log_system/tasks.py` 경로를 우선 확장한다.
- 기존 로그 race condition 대응과 idempotency 처리를 깨지 않는다.
- 기존 프론트 로그 UI가 깨지지 않도록 schema 변경 시 backward compatibility를 고려한다.
