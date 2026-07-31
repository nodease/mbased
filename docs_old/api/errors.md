# 오류 계약

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: feature/mba-89 @ 3a1d6799118f5a6bb50414914b40865e6375e35f (base dev @ 5e67adba265346009fbbc691ee16e287cd89548e, PR #142 follow-up, 2026-07-01 KST)
Related ADRs: [ADR-202606291315-resource-access-403-404-policy](../decisions/ADR-202606291315-resource-access-403-404-policy.md), [ADR-202607010220-rag-answer-trace-usage-correlation-boundary](../decisions/ADR-202607010220-rag-answer-trace-usage-correlation-boundary.md)

## 범위

공통 HTTP status, error response, reason code 기준을 정의한다.

## 현재 구현 기준

현재 Gateway는 FastAPI `HTTPException` 기반 응답을 사용한다. 기본 형태는 다음과 같다.

```json
{
  "detail": "error message"
}
```

`detail`은 string 또는 object일 수 있다. 기존 endpoint와 호환이 필요하므로 즉시 전역 envelope로 바꾸지 않는다.

모든 HTTP 응답은 요청의 `X-Request-ID` header 값을 보존하거나, 없으면 서버가 UUID를 생성해 응답 header `X-Request-ID`에 넣는다. 같은 request id는 audit metadata에도 전파된다.

현재 error body는 두 형태가 혼용된다.

| 형태 | 사용 위치 |
| --- | --- |
| `{ "detail": ... }` | 일반 `HTTPException` |
| `{ "error": { ... } }` | `apps/gateway/utils/api_errors.py`의 `raise_api_error`/`error_response`, request validation error |

전역 `HTTPException` handler는 `detail`이 이미 `{ "error": ... }` 구조이면 그대로 반환하고, 아니면 `{ "detail": ... }`로 감싼다.

## 목표 Error Envelope

신규 또는 정리 대상 API는 아래 형태를 목표로 한다.

```json
{
  "error": {
    "code": "permission.denied",
    "message": "요청한 작업을 수행할 권한이 없습니다.",
    "request_id": "req_xxx",
    "details": {}
  }
}
```

## HTTP Status

| Status | 의미 |
| --- | --- |
| `400` | request validation 외의 잘못된 입력 |
| `401` | 인증되지 않음 |
| `403` | 인증됐지만 권한 없음 |
| `404` | 리소스 없음 또는 접근 가능한 scope 안에 없음 |
| `409` | 중복 또는 상태 충돌 |
| `422` | Pydantic/FastAPI validation 실패 |
| `500` | 서버 내부 오류 |

## Resource 접근 403/404 경계

App/Workflow 같은 organization-scoped resource는 아래 기준을 따른다.

- 리소스가 없으면 `404 resource.not_found`를 반환한다.
- 요청 user가 리소스의 organization scope 밖이면 `404 resource.not_found`로 숨긴다.
- 요청 user가 같은 organization scope 안에 있지만 필요한 resource action 권한이 없으면 `403 permission.denied`를 반환한다.
- 목록 API는 접근 가능한 resource만 반환하고 숨겨진 resource 수는 노출하지 않는다.

현재 App/Workflow endpoint의 403/404 응답 body는 기존 호환을 위해 plain FastAPI `detail` 형식이다.

```json
{
  "detail": "Forbidden"
}
```

```json
{
  "detail": "App not found"
}
```

위 reason code는 정책과 목표 envelope 기준이며, App/Workflow plain `detail` 응답을 전역 envelope로 정렬하는 작업은 별도 변경으로 다룬다.

## Reason Code

| Code | HTTP | 설명 |
| --- | --- | --- |
| `auth.required` | `401` | session/token 없음 |
| `auth.invalid` | `401` | session/token invalid |
| `permission.denied` | `403` | resource permission 부족 |
| `policy.blocked` | `403` | 같은 scope 안 요청이 data/model/trace/RAG policy에 의해 차단됨 |
| `organization.required` | `400` | active organization이 필요하지만 결정되지 않음 |
| `resource.not_found` | `404` | 리소스 없음 |
| `resource.conflict` | `409` | 중복 또는 상태 충돌 |
| `validation.failed` | `400` 또는 `422` | request schema validation 실패 또는 endpoint/service 단계의 의미상 validation 실패 |
| `hierarchy_unavailable` | `422` | 명시적으로 요청한 hierarchical retrieval data/index가 없음 |
| `invalid_chunking_mode` | `400` | upload form의 chunking mode 값이 허용 범위를 벗어남 |
| `invalid_chunking_selection` | `400` | chunking mode와 selection option 조합이 지원되지 않음 |
| `unsupported_chunking_mode_for_source` | `400` | 요청 source type에서 해당 chunking mode를 지원하지 않음 |
| `invalid_correlation_id` | `400` | client가 제공한 correlation id가 길이/문자셋/보안 규칙을 만족하지 않음 |
| `credential_selection_required` | `409` | 후속 default credential/preset 자동 선택에서 사용할 LLM credential/model을 deterministic하게 선택할 수 없음 |
| `provider.timeout` | `504` 또는 SSE terminal `error` | 외부 LLM provider 호출이 endpoint별 timeout cap을 초과함. Stream 시작 후에는 terminal event reason code로 전달 |
| `stream.timeout` | SSE terminal `error` | SSE stream이 endpoint별 stream/retrieval timeout cap을 초과함. Stream 시작 후에는 HTTP status를 바꾸지 않음 |
| `generation.failed` | `500` | RAG Agent answer 또는 LLM generation 처리 중 sanitized internal failure가 발생함. Retrieval 내부 예외, provider/generation generic exception, invalid credential config, 기타 Agent answer 내부 실패를 포함한다. |
| `secret.not_returnable` | `500` 또는 `403` | secret 원문 반환 시도 차단 |

`generation.failed` 응답은 내부 실패를 사용자에게 노출하지 않는 안전한 envelope다. 응답 message/details에는 raw credential, `encrypted_config`, API key, token, provider raw error, stack trace, raw prompt/completion을 포함하지 않고, 필요한 경우 `request_id`, `answer_run_id`, `correlation_id` 같은 safe identifier만 포함한다. 원인 세부사항은 server log/observability에만 남긴다.

## 보안 규칙

- secret, token, credential, raw API key 원문은 error message에 포함하지 않는다.
- 현재 일부 legacy/helper endpoint가 내부 예외 문자열을 `detail` 또는 응답 field에 포함하는 경우에는 각 API 문서에 current behavior로 명시한다. 운영 목표 계약은 sanitized error code와 request/correlation id만 반환하고 내부 예외 세부 내용은 server log/observability에만 남기는 것이다.
- 401/403은 `audit_logs`에 기록할 수 있다.
- 401/403 `HTTPException`은 해당 예외에 `audit_recorded`가 없으면 Gateway exception handler에서 permission denied audit를 기록한다.
- Service/helper가 `permission.denied` 또는 `policy.block` audit을 직접 기록한 뒤 401/403을 반환할 때는 예외에 `audit_recorded=True` 또는 동등 marker를 설정해 전역 handler의 중복 `auth.permission_denied` 기록을 막아야 한다.
- permission 실패 metadata에는 resource/action/effective permission 정도만 남기고 secret payload를 남기지 않는다.
