# Trace API 설계

## 목적

Trace 조회, payload 조회, tracing policy 관리를 위한 API 설계 방향을 정의한다.

1차 전체 구현에서는 신규 trace router를 후속으로 미루지 않고 구현 범위에 포함한다.

## 현재 API

현재 workflow endpoint에는 다음 실행 로그 API가 있다.

- `GET /api/v1/workflows/{workflow_id}/runs`
- `GET /api/v1/workflows/{workflow_id}/runs/{run_id}`
- `GET /api/v1/workflows/{workflow_id}/stats`

기존 API는 호환성을 위해 유지한다.

## 신규 Trace Router

신규 router 후보:

- `apps/gateway/api/v1/endpoints/tracing.py`
- prefix: `/api/v1`

필수 endpoint:

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

## View level

Trace 조회 API는 다음 조회 수준을 지원한다.

- `metadata`
- `redacted`
- `raw`

정책:

- 권한 없는 `raw` 요청은 기본적으로 403을 반환한다.
- 자동 redacted downgrade는 명시 정책이 있을 때만 허용한다.
- raw payload 조회 시도는 허용/차단 모두 `trace_payload_access_events`에 기록한다.

## Query parameter

Trace 목록:

- `status`
- `trigger_mode`
- `from`
- `to`
- `app_id`
- `workflow_id`
- `deployment_id`
- `user_id`
- `node_type`
- `correlation_id`
- `limit`
- `page`

Trace 목록 응답은 기존 호환을 위해 `total`, `items`를 유지한다. 권한 판정 때문에 서버가 scan limit에 도달하면 다음 필드로 추정 여부를 알린다.

- `has_more`
- `total_is_estimated`
- `scan_limit_reached`

Trace 상세:

- `view=metadata|redacted|raw`
- `include_spans=true|false`
- `include_payloads=true|false`
- `include_llm=true|false`
- `include_rag=true|false`
- `include_guardrail=true|false`

Payload 조회:

- `view=metadata|redacted|raw`
- `payload_kind`
- `node_run_id`
- `history=true|false`
- `latest=true|false`

Policy 조회:

- `scope_type=global|organization|app`
- `scope_id`

## 응답 모델 후보

### Trace summary

```json
{
  "id": "...",
  "workflow_id": "...",
  "app_id": "...",
  "user_id": "...",
  "deployment_id": "...",
  "status": "success",
  "trigger_mode": "manual",
  "started_at": "...",
  "finished_at": "...",
  "duration": 1.23,
  "total_tokens": 1000,
  "total_cost": 0.01,
  "redaction_applied": true,
  "pii_detected": false,
  "payload_storage_mode": "redacted_only"
}
```

### Trace detail

```json
{
  "id": "...",
  "workflow_id": "...",
  "app_id": "...",
  "status": "success",
  "inputs": {},
  "outputs": {},
  "error_message": null,
  "trace_metadata": {},
  "spans": [],
  "payloads": []
}
```

`inputs`와 `outputs`는 요청 view level에 따라 metadata-only, redacted, raw 중 하나를 반환한다. raw 권한이 없으면 403이다.

metadata view에서는 `inputs`, `outputs`, `error_message`를 반환하지 않는다. redacted/raw view의 `error_message`도 원문 payload 경로가 아니라 기존 UI 호환용 redacted field로만 취급한다.

### Span detail

```json
{
  "id": "...",
  "trace_id": "...",
  "node_id": "...",
  "node_type": "llmNode",
  "status": "success",
  "started_at": "...",
  "finished_at": "...",
  "duration": 0.5,
  "inputs": {},
  "outputs": {},
  "process_data": {},
  "trace_metadata": {}
}
```

metadata view에서는 `inputs`, `outputs`, `process_data`를 반환하지 않는다. `trace_metadata`는 node type별 allowlist sanitizer를 통과한 요약 필드만 반환한다.

### Payload detail

```json
{
  "id": "...",
  "trace_id": "...",
  "span_id": "...",
  "payload_kind": "prompt",
  "view": "redacted",
  "payload": {},
  "redaction_applied": true,
  "pii_detected": true,
  "secret_detected": false,
  "redaction_metadata": {
    "fields": ["prompt.user.email"],
    "rule_ids": ["email"]
  }
}
```

기본 동작:

- `latest=true`가 기본이다.
- `history=true`를 명시하면 append-only payload history를 반환한다.
- history 정렬은 `sequence`, `attempt`, `created_at` 순서를 따른다.

### Policy response

```json
{
  "id": "...",
  "scope_type": "app",
  "scope_id": "...",
  "is_active": true,
  "updated_by": "...",
  "updated_at": "..."
}
```

## Error 응답

- 400: 잘못된 view level 또는 policy 값
- 401: 인증 없음
- 403: 정책상 조회 불가
- 404: trace, payload, policy 없음
- 409: 충돌하는 policy scope

## Retention purge API

`POST /api/v1/tracing/retention/purge`

역할:

- system admin이 retention purge Celery task를 수동으로 실행한다.
- 자동 스케줄러가 아니라 명시적 운영 작업으로 실행한다.

요청 후보:

```json
{
  "scope_type": "global",
  "scope_id": null,
  "dry_run": true,
  "limit": 1000
}
```

응답 후보:

```json
{
  "task_id": "celery-task-id",
  "dry_run": true,
  "status": "queued"
}
```

## Service 계층

Endpoint에 직접 정책 로직을 넣지 않는다.

필수 service 후보:

- `TraceQueryService`
- `TraceAccessService`
- `TraceRedactionService`
- `TracePolicyService`
- `TracePayloadService`
- `TraceRetentionService`

역할:

- endpoint는 요청 parsing과 response 반환
- service는 권한 확인, policy 적용, redaction view 결정
- repository 또는 query helper는 DB 조회
- payload access event 기록은 service에서 수행

## 기존 workflow run API와의 관계

- 기존 run detail API는 기존 UI 호환을 위해 유지한다.
- 신규 optional field만 추가한다.
- 신규 trace router가 상세 tracing UI/API의 기준이 된다.
- 기존 API에서 raw payload를 새로 노출하지 않는다.
- 신규 trace API에서 payload는 `trace_payloads`를 source of truth로 사용한다.

## 영향 파일 후보

- `apps/gateway/api/v1/endpoints/workflow.py`
- 신규 `apps/gateway/api/v1/endpoints/tracing.py`
- `apps/gateway/api/api.py`
- `apps/gateway/services/*`
- `apps/shared/schemas/log.py`
- 신규 `apps/shared/schemas/tracing.py`
