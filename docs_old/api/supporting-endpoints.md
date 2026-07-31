# Supporting Endpoint API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 범위

주요 제품 도메인 문서에 속하지 않는 health, DB connector, prompt/code/template wizard helper API 계약을 정의한다.

## Health

| Status | Method | Path | Request | Response | 설명 |
| --- | --- | --- | --- | --- | --- |
| Implemented | `GET` | `/api/v1/health` | 없음 | health payload | Gateway 및 DB health check |

Gateway health 응답은 DB `SELECT 1`까지 확인한다.

성공:

```json
{
  "status": "ok",
  "service": "Moduly API",
  "database": "ok"
}
```

현재 코드 기준 DB 연결 실패 시 HTTP status는 `503`이고 `database` 값에 내부 예외 문자열이 포함될 수 있다. 이는 current behavior 기록이며, 운영 목표 계약은 sanitized status/error code와 request/correlation id만 반환하고 내부 DB 오류 세부 내용은 server log/observability에만 남기는 것이다.

Nginx 컨테이너의 `/health`는 Gateway health가 아니라 단순 reverse proxy health endpoint다. 현재 `docker/nginx/nginx.conf`는 `/health`에 대해 plain text `OK`를 즉시 반환한다.

## Connector

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/connectors/test` | `DBConnectionTestRequest` | `DBConnectionTestResponse` | unauthenticated connection test helper |
| Implemented | `POST` | `/api/v1/connectors` | `DBConnectionTestRequest` | `{ "id": string, "success": boolean, "message": string }` | authenticated; connection owner becomes current user |
| Implemented | `GET` | `/api/v1/connectors/{connection_id}` | 없음 | `DBConnectionDetailResponse` | connection owner |
| Implemented | `GET` | `/api/v1/connectors/{connection_id}/schema` | 없음 | `{ "tables": [...] }` schema payload | connection owner |

Connector response는 DB/SSH password, private key 같은 secret 원문을 반환하지 않는다. 현재 connector owner 기준 API는 MVP 2 connection policy 정렬 전의 current-user owner scope다.

현재 지원 DB 타입은 `postgres`뿐이다. `POST /api/v1/connectors/test`는 인증 dependency가 없고 외부 DB/SSH host로 연결을 시도한다. 실패 응답은 현재 내부 예외 문자열을 포함할 수 있으므로, 운영 노출 전에는 인증 또는 내부망 제한, rate limit, 명시 timeout, egress allowlist, private/link-local/metadata network 차단, sanitized error response, secret value를 제외한 audit/structured logging을 적용해야 한다.

`POST /api/v1/connectors`는 저장 전 연결 테스트를 다시 수행한 뒤 DB/SSH secret을 암호화해서 저장한다. 현재 저장 전 연결 테스트에는 10초 timeout이 적용되어 있고, 실패 응답은 내부 예외 문자열을 포함할 수 있다. 운영 목표는 내부 예외 문자열 대신 sanitized error code를 반환하는 것이다.

`GET /api/v1/connectors/{connection_id}/schema`는 owner 확인 후 서버 내부에서 secret을 복호화해 DB에 접속한다. secret은 API 응답에 포함하지 않는다. 다만 table/column/schema 이름은 기업 환경에서 민감 metadata일 수 있으므로 raw schema payload를 server log, cache, audit metadata에 그대로 남기지 않는다. 필요하면 table count, selected table id, schema hash 같은 요약 field만 기록한다.

## Wizard Helpers

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `GET` | `/api/v1/prompt-wizard/check-credentials` | optional query `organization_id` | `{ "has_credentials": boolean }` | authenticated; LLM credential `use` + verified credential-model relation |
| Implemented | `POST` | `/api/v1/prompt-wizard/improve` | `PromptImproveRequest` with optional `organization_id` | `PromptImproveResponse` | authenticated; LLM credential `use` + verified credential-model relation |
| Implemented | `GET` | `/api/v1/code-wizard/check-credentials` | optional query `organization_id` | `{ "has_credentials": boolean }` | authenticated; LLM credential `use` + verified credential-model relation |
| Implemented | `POST` | `/api/v1/code-wizard/generate` | `CodeGenerateRequest` with optional `organization_id` | `CodeGenerateResponse` | authenticated; LLM credential `use` + verified credential-model relation |
| Implemented | `GET` | `/api/v1/template-wizard/check-credentials` | optional query `organization_id` | `{ "has_credentials": boolean }` | authenticated; LLM credential `use` + verified credential-model relation |
| Implemented | `POST` | `/api/v1/template-wizard/improve` | `TemplateImproveRequest` with optional `organization_id` | `TemplateImproveResponse` | authenticated; LLM credential `use` + verified credential-model relation |

Wizard helper API는 MBA-43 기준에서 authenticated LLM runtime으로 취급한다. Runtime credential은 valid credential, resolved organization scope, current user의 credential `use` 권한, active model, verified credential-model relation을 모두 만족해야 한다.

Wizard organization scope는 LLM credential 기본 organization 정책과 정렬한다. 요청에 `organization_id`가 있으면 그 값을 사용하고, 없으면 current user의 default organization fallback을 사용한다. default organization foundation이 없으면 기존 fallback 경로가 organization/team/membership row를 생성할 수 있다.

Wizard runtime은 provider별 효율 모델 map의 순서를 먼저 따른다. 선택된 provider/model 후보에서 여러 credential이 같은 Wizard model을 실행할 수 있으면 `llm_rel_credential_models.priority ASC`, `llm_credentials.created_at ASC`, `llm_credentials.id ASC` 순서로 runtime credential을 선택한다.

현재 wizard helper는 provider별로 효율 모델을 고정 선택한다. 코드/템플릿 위저드는 endpoint 내부 map을 사용하고, 프롬프트 위저드는 `LLMService.EFFICIENT_MODELS`를 사용한다. 사용할 수 있는 runtime credential이 없으면 POST endpoint는 기존처럼 `400`과 `credentials_required=true` 성격의 detail을 반환하고 runtime block audit은 `permission.denied`로 기록한다. GET check-credentials는 audit을 기록하지 않고 `{ "has_credentials": false }`를 반환한다.
