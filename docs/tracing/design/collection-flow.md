# 데이터 수집 흐름

## 목적

Tracing 데이터가 어떤 경로로 생성되고 저장되어야 하는지 정의한다.

## 전체 흐름

```text
Client or External Trigger
  -> Gateway API
  -> Celery workflow task
  -> WorkflowEngine
  -> Node.execute()
  -> RedactionService
  -> WorkflowLogger
  -> Celery log task
  -> LogSystem
  -> PostgreSQL
```

1차 전체 구현은 Gateway 이후 백엔드 실행 흐름을 수집한다. 브라우저 canvas interaction은 수집하지 않는다.

## Trace 생성

Trace는 workflow 실행 시작 시 생성한다.

현재 위치:

- `WorkflowEngine._execute_core`
- `WorkflowLogger.create_run_log`
- `log_system.tasks.create_run_log`

수집 대상:

- workflow id
- app id
- user id
- deployment id
- trigger mode
- workflow version
- redacted user input
- started_at
- correlation_id
- request_id
- workflow_task_id
- redaction_policy_id
- retention_policy_id
- visibility_policy_id

## Span 생성

Span은 각 node 실행 시작 시 생성한다.

현재 위치:

- `WorkflowEngine._submit_node`
- `WorkflowLogger.create_node_log`
- `log_system.tasks.create_node_log`

수집 대상:

- workflow_run_id
- node_id
- node_type
- redacted inputs
- process_data
- started_at
- redaction metadata
- sequence
- retry_count

## Span 완료

Span은 node 실행이 성공하면 완료 처리한다.

현재 위치:

- `WorkflowEngine._execute_node_task`
- `WorkflowLogger.update_node_log_finish`
- `log_system.tasks.update_node_log_finish`

수집 대상:

- redacted outputs
- finished_at
- duration
- trace_metadata
- payload references
- redaction metadata

`trace_metadata`는 WorkflowEngine에서 node type별 allowlist sanitizer를 통과한 뒤 WorkflowLogger로 전달한다.

## Span 실패

Span은 node 실행 중 예외가 발생하면 실패 처리한다.

현재 위치:

- `WorkflowEngine._execute_node_task`
- `WorkflowLogger.update_node_log_error`
- `log_system.tasks.update_node_log_error`

수집 대상:

- redacted error_message
- error_type
- finished_at
- duration
- partial trace_metadata
- payload references
- redaction metadata

에러 원문은 metadata에 저장하지 않는다. metadata에는 `error_type`, `error_code` 같은 분류값만 저장하고, 기존 호환용 `error_message`도 redaction 후 저장한다.

## Trace 완료

Trace는 workflow 전체 실행이 성공하면 완료 처리한다.

현재 위치:

- `WorkflowEngine._execute_core`
- `WorkflowLogger.update_run_log_finish`
- `log_system.tasks.update_run_log_finish`

수집 대상:

- redacted final outputs
- finished_at
- duration
- total_tokens
- total_cost
- aggregate metadata

## Trace 실패

Trace는 workflow 실행 중 처리 불가능한 예외가 발생하면 실패 처리한다.

현재 위치:

- `WorkflowEngine._execute_core`
- `WorkflowLogger.update_run_log_error`
- `log_system.tasks.update_run_log_error`

수집 대상:

- redacted error_message
- failed_node_id
- failed_node_type
- finished_at
- duration

## Redaction 위치

민감정보는 Celery broker에도 남을 수 있으므로 Celery log task 전송 전에 redaction한다.

처리 원칙:

- Redaction service 구현 전에는 payload 저장 범위를 확장하지 않는다.
- 신규 raw field를 추가할 때는 redaction path와 encryption path를 먼저 구현한다.
- secret 계열 값은 redaction policy와 무관하게 원문 저장 금지다.
- redacted copy는 기존 compatibility field와 `trace_payloads`에 저장한다.
- raw payload는 암호화된 별도 field에만 저장한다.
- raw payload 암호화 실패 시 raw 저장으로 fallback하지 않고 redacted copy만 저장하며 전용 로그/메트릭을 남긴다.

## Metadata sanitizer 위치

`trace_metadata`는 payload 저장소가 아니므로 저장 전/저장 시/반환 시 3중 방어를 적용한다.

```text
WorkflowEngine / WorkflowLogger
  -> scope별 allowlist sanitizer
  -> Celery log task payload
  -> LogSystem 저장 직전 sanitizer
  -> PostgreSQL trace_metadata
  -> TraceQueryService 반환 직전 sanitizer
```

scope별 sanitizer:

- `sanitize_run_metadata()`
- `sanitize_span_metadata(node_type, metadata)`
- `sanitize_retention_metadata()`
- `sanitize_gateway_metadata()`

allowlist에 명시된 exact field는 유지하고, 임의 하위 key에는 민감 key/pattern denylist를 적용한다.

## Node type별 metadata schema

### LLM

`process_data`:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "credential_id": "credential-id",
  "parameters": {
    "temperature": 0.7,
    "top_p": 1,
    "max_tokens": 4096,
    "presence_penalty": 0,
    "frequency_penalty": 0
  },
  "fallback_model_id": "gpt-4o-mini"
}
```

`trace_metadata`:

```json
{
  "llm": {
    "provider": "openai",
    "model": "gpt-4o-mini",
    "prompt_payload_id": "payload-id",
    "completion_payload_id": "payload-id",
    "prompt_tokens": 100,
    "completion_tokens": 50,
    "total_tokens": 150,
    "total_cost": 0.001,
    "latency_ms": 850,
    "retry_count": 0
  }
}
```

### RAG

`process_data`:

```json
{
  "knowledge_base_ids": ["kb-id"],
  "top_k": 5,
  "similarity_threshold": 0.7,
  "embedding_model": "text-embedding-3-small",
  "chunking_strategy": "document_default"
}
```

`trace_metadata`:

```json
{
  "rag": {
    "retrieval_results": [
      {
        "knowledge_base_id": "kb-id",
        "document_id": "doc-id",
        "filename": "guide.pdf",
        "page_number": 3,
        "similarity_score": 0.91
      }
    ],
    "latency_ms": 120,
    "retrieved_context_payload_id": "payload-id"
  }
}
```

RAG 원문 chunk, text, body, prompt, response는 metadata에 저장하지 않는다. retrieved context 본문은 `retrieved_context` payload로만 저장한다.

### HTTP

`process_data`:

```json
{
  "method": "GET",
  "host": "api.example.com",
  "path": "/v1/items",
  "timeout_ms": 5000
}
```

`trace_metadata`:

```json
{
  "http": {
    "method": "GET",
    "host": "api.example.com",
    "path": "/v1/items",
    "status_code": 200,
    "latency_ms": 240,
    "request_size": 120,
    "response_size": 4096,
    "retry_count": 0,
    "request_payload_id": "payload-id",
    "response_payload_id": "payload-id"
  }
}
```

### Code/Sandbox

`process_data`:

```json
{
  "timeout": 10,
  "language": "python"
}
```

`trace_metadata`:

```json
{
  "sandbox": {
    "execution_time_ms": 43,
    "exit_code": 0,
    "timeout": false,
    "stdout_payload_id": "payload-id",
    "stderr_payload_id": "payload-id"
  }
}
```

### Workflow/Submodule

`process_data`:

```json
{
  "child_app_id": "app-id",
  "deployment_id": "deployment-id",
  "input_mapping": {}
}
```

`trace_metadata`:

```json
{
  "workflow": {
    "child_workflow_id": "workflow-id",
    "child_workflow_run_id": "run-id",
    "parent_node_run_id": "node-run-id",
    "output_mapping": {},
    "latency_ms": 1500
  }
}
```

### Moduly Guardrail

`process_data`:

```json
{
  "policy_id": "policy-id",
  "rules": ["rule-id"],
  "mode": "block"
}
```

`trace_metadata`:

```json
{
  "guardrail": {
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
}
```

전역 AWS Bedrock Guardrail 결과 payload는 내부 trace에 저장하지 않는다.

## Idempotency

- 같은 `run_id` create 중복은 성공으로 간주할 수 있다.
- 같은 `node_run_id` create 중복은 성공으로 간주할 수 있다.
- finish/error가 create보다 먼저 도착할 수 있으므로 upsert 또는 retry를 유지한다.
- 신규 metadata update는 기존 idempotency를 깨면 안 된다.
- payload 저장은 append-only를 기본으로 한다.
- retry, streaming, partial output, 재실행 payload는 새 row로 추가한다.
- payload 조회 API는 기본적으로 latest payload를 반환하고, history 조회가 필요하면 `sequence`, `attempt`, `created_at` 기준으로 정렬한다.
