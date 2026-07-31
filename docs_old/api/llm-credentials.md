# LLM 자격 증명 API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: feature/mba-89 @ 3a1d6799118f5a6bb50414914b40865e6375e35f (base dev @ 5e67adba265346009fbbc691ee16e287cd89548e, PR #142 follow-up, 2026-07-01 KST)
Related ADRs: [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md)

## 범위

LLM provider, model, credential, model pricing, credential-model sync 계약을 정의한다.

## 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `GET` | `/api/v1/llm/providers` | 없음 | `LLMProviderResponse[]` | authenticated |
| Implemented | `GET` | `/api/v1/llm/my-models` | 없음 | `LLMModelResponse[]` | credential `use` + verified credential-model relation |
| Implemented | `GET` | `/api/v1/llm/my-embedding-models` | 없음 | `LLMModelResponse[]` | credential `use` + verified credential-model relation |
| Implemented | `GET` | `/api/v1/llm/credentials` | 없음 | `LLMCredentialResponse[]` | credential `read` |
| Implemented | `GET` | `/api/v1/llm/agent-answer-options` | 없음 | `LLMCredentialModelOptionResponse[]` | `X-Organization-Id` 필수; credential `use` + verified credential-model relation |
| Implemented | `POST` | `/api/v1/llm/credentials` | `LLMCredentialCreate` | `LLMCredentialResponse` | organization `manager` |
| Implemented | `DELETE` | `/api/v1/llm/credentials/{credential_id}` | 없음 | message | credential `write` |
| Implemented | `POST` | `/api/v1/llm/credentials/{credential_id}/sync-models` | 없음 | sync result | credential `write` |
| Implemented | `GET` | `/api/v1/llm/stats/top-models` | query | stats | authenticated |
| Implemented | `POST` | `/api/v1/llm/models/sync-pricing` | 없음 | result | system admin |
| Implemented | `PUT` | `/api/v1/llm/models/{model_id}/pricing` | `LLMModelPricingUpdate` | result | system admin |

현재 구현 세부사항:

- `GET /api/v1/llm/credentials`는 전체 valid credential 후보 중 현재 user가 `read` 권한을 가진 credential만 반환한다.
- `GET /api/v1/llm/my-models`와 `/my-embedding-models`는 `llm_rel_credential_models.is_verified == true`, `llm_models.is_active == true`, credential `use` 권한을 함께 만족하는 모델만 반환한다.
- `GET /api/v1/llm/agent-answer-options`는 RAG Agent answer UI가 바로 제출할 수 있는 verified model/credential pair만 반환한다. 서버는 `X-Organization-Id`로 active organization scope를 확인하고, 같은 organization의 valid credential, `llm_rel_credential_models.is_verified == true`, active chat model, credential `use` 권한을 모두 만족하는 조합만 포함한다. 모델과 credential을 독립 목록으로 받아 클라이언트에서 임의 조합하는 것은 Agent answer 기본 UI 계약이 아니다. 응답에는 `encrypted_config`, raw API key, token, credential secret value를 포함하지 않는다.
- `POST /api/v1/llm/credentials/{credential_id}/sync-models`는 `purge_unverified` query를 지원한다. 원격 모델 목록을 다시 가져온 뒤 기존 mapping을 fail-closed로 unverified 처리하고, 원격에 있는 모델만 verified로 복구한다.
- `DELETE /api/v1/llm/credentials/{credential_id}`는 row를 물리 삭제하지 않고 `is_valid=false`로 비활성화한다.
- pricing 관리 API의 `system admin` 판정은 trace access의 `TraceRbacService` provider에 위임한다. 현재 기본 provider는 deny-all이므로 별도 provider를 설정하지 않은 런타임에서는 `403 system_admin_required`로 차단된다.
- 현재 `LLMCredential.encrypted_config`는 column 이름과 달리 `{"apiKey": "...", "baseUrl": "..."}` 형태의 JSON string을 그대로 저장한다. 실제 암호화 적용은 보안 목표 상태이며, 현재 응답에는 `config_preview`만 노출된다.

Runtime credential 사용 기준:

- LLM runtime은 credential `use` 권한, `llm_rel_credential_models.is_verified == true`, `llm_models.is_active == true`를 모두 만족해야 한다.
- Workflow Engine LLM runtime은 명시적으로 전달된 valid `organization_id`를 요구한다. 사용자의 default organization id도 명시적으로 전달되면 유효한 runtime scope로 본다.
- Workflow Engine LLM runtime은 `llm_credentials.organization_id IS NULL` legacy credential을 사용하지 않는다. legacy null credential은 organization backfill 또는 reassignment 이후 runtime 후보가 될 수 있다.
- Gateway service-level LLM helper에서 `organization_id`가 없는 호출은 현재 legacy compatibility 경로로 남아 있다. 모든 LLM runtime path의 `X-Organization-Id` 필수화와 mutation 없는 fallback 전환은 후속 API/policy 변경이다.
- Runtime에서 선택된 credential id는 성공한 workflow LLM node usage logging에 그대로 전달해야 하며, 실행 후 usage logging 단계에서 credential을 다시 선택하지 않는다.

## 스키마

### `LLMCredentialCreate`

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `provider_id` | UUID | Yes | provider id |
| `organization_id` | UUID | No | credential을 저장할 organization id. 없으면 현재 user의 기본 organization을 사용 |
| `credential_name` | string | Yes | 표시 이름 |
| `api_key` | string | Yes | 원문 API key. 목표 상태에서는 저장 전 암호화해야 한다. 현재 구현은 JSON string으로 저장한다. |

`api_key`는 응답, 로그, audit metadata에 원문으로 남기지 않는다.

### `LLMCredentialResponse`

| Field | Type | 설명 |
| --- | --- | --- |
| `id` | UUID | credential id |
| `provider_id` | UUID | provider id |
| `user_id` | UUID | 생성 user |
| `organization_id` | UUID \| null | credential organization scope |
| `credential_name` | string | 표시 이름 |
| `config_preview` | string | masking된 key preview |
| `is_valid` | boolean | 검증 상태 |
| `quota_type` | string | quota 유형 |
| `quota_limit` | integer | quota limit |
| `quota_used` | integer | quota used |
| `created_at` | datetime | 생성 시각 |
| `updated_at` | datetime | 수정 시각 |

### `LLMCredentialModelOptionResponse`

| Field | Type | 설명 |
| --- | --- | --- |
| `model` | `LLMModelResponse` | verified relation을 가진 active chat model |
| `credential` | `LLMCredentialOptionResponse` | 같은 active organization 안에서 `use` 가능한 valid credential의 선택용 safe field |
| `provider_name` | string | model provider 이름 |
| `relation_priority` | integer | `llm_rel_credential_models.priority` |

### `LLMCredentialOptionResponse`

| Field | Type | 설명 |
| --- | --- | --- |
| `id` | UUID | request `credential_id`로 제출할 credential id |
| `provider_id` | UUID | provider id |
| `organization_id` | UUID/null | credential이 속한 organization |
| `credential_name` | string | 표시 이름 |
| `config_preview` | string/null | masking된 key preview |
| `is_valid` | boolean | 검증 상태 |

이 응답은 Agent answer request에 사용할 `(generation_model_id, credential_id)` 선택지를 제공하기 위한 UI-facing allowlist다. 각 row는 verified relation과 credential `use` 권한을 통과한 조합이어야 하며, credential 원문 조회 권한을 부여하지 않는다. `credential` field는 표시/선택에 필요한 safe field만 포함하고 `user_id`, quota, timestamps, `encrypted_config`, API key 원문, token, provider secret은 반환하지 않는다.

## MVP 1 변경 기준

- credential 조회/사용/삭제/sync는 organization scope와 credential permission을 기준으로 판정한다.
- 현재 코드에서 삭제와 sync는 `ensure_llm_credential_permission(..., "write")`를 호출한다. `write`의 최소 상태는 `manager`다.
- workflow engine LLM node는 credential `use` 권한이 없으면 실행을 차단한다.
- model 전용 permission table은 만들지 않는다. credential 권한과 `llm_rel_credential_models` 검증 상태로 사용 가능 모델을 제한한다.
- Application-level model blacklist/allowlist 정책은 이 문서의 MBA-43 runtime 기준에 포함하지 않는다. 해당 정책을 도입하면 저장 위치와 `policy.block` 사용 기준을 별도 문서에서 먼저 확정한다.
