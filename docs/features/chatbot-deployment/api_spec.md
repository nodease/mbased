# Chatbot Deployment API Spec

Status: Draft

챗봇 배포는 기존 배포/공개 실행 엔드포인트의 계약을 확장한다. 현재 `internal_chatbot`은 인증 deployment run/run-info endpoint에서 active membership·workflow `execute` 확인과 로그인 사용자의 execution subject 전달을 적용한다. Target private-RAG 내부 Chatbot access grant와 session namespace는 이 current contract 위에 추가되는 후속 기능이며, 동일한 완성 상태로 간주하지 않는다.

공개 route는 업무 `inputs`와 분리한 bounded `conversation.history`를 매 요청에 전달하며 `memory_mode`, browser `conversation_id`, server-side transcript와 execution-log memory를 사용하지 않는다. 인증 내부 route는 별도의 bounded `conversation.client_id`를 사용하며 durable session/persistence 계약은 후속 범위다. 세부 계약은 [Conversation Memory API spec](../conversation-memory/api_spec.md)을 따른다.

## Endpoints

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| POST | `/api/v1/deployments` | 배포 생성. `type: "chatbot"` 또는 `"internal_chatbot"` 지원 | 로그인 + workflow `deploy` 권한 |
| GET | `/api/v1/deployments/{deployment_id}/run-info` | 실행 화면용 safe 배포 정보 조회. secret/graph snapshot 제외 | 로그인 + workflow `execute` 권한 |
| POST | `/api/v1/deployments/{deployment_id}/run` | 활성 배포 snapshot을 로그인 사용자 권한 주체로 실행 | 로그인 + workflow `execute` 권한 + `application/json` |
| POST | `/api/v1/deployments/{source_deployment_id}/browser-access-revisions` | source snapshot을 복제해 browser policy 새 version 생성 | 로그인 + workflow `deploy` 권한 |
| GET | `/api/v1/deployments/public/{url_slug}/browser-access` | iframe CSP용 active policy safe projection | 없음, `Cache-Control: no-store` |
| GET | `/api/v1/deployments/public/{url_slug}/info` | 공개 배포 정보(`type: "chatbot"` 포함) | 없음. Endpoint-level wildcard CORS 없음 |
| POST | `/api/v1/run-public/{url_slug}/chat` | bounded client-held history로 챗봇 공개 실행 | 없음. 모든 응답에서 CORS grant 제거, no-store/no-referrer |

## Request And Response Models

### DeploymentType

`api | webapp | widget | mcp | workflow_node | schedule | webhook | chatbot | internal_chatbot`

- `chatbot` 값이 추가되었다. Postgres enum에는 멤버 이름 `CHATBOT`으로 저장되고, 응답에는 `deployment.type.value`인 `"chatbot"`(소문자)로 직렬화된다.
- `internal_chatbot`은 Postgres enum 멤버 `INTERNAL_CHATBOT`으로 저장되고 응답에는 `"internal_chatbot"`으로 직렬화된다. Public info와 `/run-public`에서는 노출·실행하지 않는다.
- Public chatbot deployment activation은 [deployment](../deployment/api_spec.md)의 preflight 계약을 따른다. `/run-public`은 사용자 subject를 주입하지 않으므로 private KB 후보가 있으면 활성 배포 create/toggle에서 `409 deployment.preflight.blocked`로 차단되어야 한다.

## Target Runtime Surface Separation

- `public_chatbot`: Conversation Access Grant나 server session 없이 client-held history만 사용하고 login cookie가 있어도 anonymous public-only RAG로 평가한다.
- `authenticated_internal_chatbot`: 현재는 cookie authentication, configured credentialed JSON/CORS 경계, active membership, workflow `execute`, current user KB permission으로 실행한다. 현재 구현을 CSRF token/exact-Origin 완료로 표현하지 않는다. Target에서는 별도 내부 Chatbot 이용 권한, CSRF token, exact Origin과 독립 Conversation Session namespace를 추가한다.
- 두 surface는 시각 Chatbot component만 재사용한다. Public route의 authentication/audience를 조건부 완화하거나 public grant를 execution subject로 승격하지 않는다.
- Public iframe parent는 [ADR-0043](../../decisions/ADR-0043-deployment-browser-origin-and-embedding-boundary.md)의 deployment-owned versioned `browser_access_policy`와 CSP `frame-ancestors`가 소유한다. Iframe first-party API와 external direct JavaScript CORS는 별도 경계이며 client/environment fallback으로 parent를 허용하지 않는다.

## Browser Access Policy

`POST /api/v1/deployments`와 `POST /api/v1/deployments/preflight`는 다음 optional field를 받는다.

```json
{
  "browser_access_policy": {
    "contract_version": "deployment_browser_access.v1",
    "embedding": {
      "enabled": true,
      "parent_origins": ["https://portal.example.com"]
    }
  }
}
```

- `chatbot`/`widget`에서 생략하면 disabled + empty origins canonical policy를 저장한다.
- `internal_chatbot`과 다른 deployment type에서 non-null policy를 보내면 `422 deployment.browser_access.not_supported`다.
- `enabled=true`는 canonical parent 1~20개, `enabled=false`는 empty list만 허용한다.
- Production은 exact HTTPS, dev/test HTTP는 `localhost`, `127.0.0.1`, `[::1]`만 허용한다.
- Server는 UTS #46 non-transitional + STD3 DNS A-label, canonical IPv4/IPv6와 default port를 정규화한다.
- wildcard/null/local scheme/path/query/fragment/userinfo/control/trailing-dot/legacy IP, canonical duplicate와 4096-byte CSP value 초과를 거부한다.
- Preflight response는 valid `chatbot`/`widget` 입력의 canonical `normalized_browser_access_policy`를 `passed`, `warning`, `blocked` 상태와 별개로 반환한다. Client normalization은 저장 권위가 아니다.

### Browser Policy Revision

```http
POST /api/v1/deployments/{source_deployment_id}/browser-access-revisions
Content-Type: application/json

{
  "browser_access_policy": {
    "contract_version": "deployment_browser_access.v1",
    "embedding": {
      "enabled": true,
      "parent_origins": ["https://new-portal.example.com"]
    }
  },
  "is_active": false
}
```

Response는 `201 DeploymentResponse`다. Source의 app/type/graph snapshot/config/input/output schema/description을 복제하고 새 version과 policy를 저장하며 source row를 수정하지 않는다. `is_active=false`가 기본값이고 active 요청은 기존 activation preflight/lifecycle lock/single-active transaction을 사용한다. Missing/cross-scope source는 기존 safe 404, wrong type은 422다.

### Public Browser Policy Projection

```http
GET /api/v1/deployments/public/{url_slug}/browser-access
```

```json
{
  "contract_version": "deployment_browser_access.v1",
  "deployment_version": 3,
  "embedding": {
    "enabled": true,
    "frame_ancestors": ["https://portal.example.com"]
  }
}
```

Gateway는 active pointer, app ownership, active 상태와 `type in {chatbot, widget}`을 함께 검사한다. Legacy null 또는 malformed/unknown persisted policy는 raw 값을 노출하지 않고 disabled projection을 반환한다. Missing/inactive/wrong type은 safe 404다. Response에는 graph, secret, app/organization/KB/internal ID가 없으며 wildcard CORS를 추가하지 않는다.

Next `/embed/chat/{slug}` response boundary는 projection을 최대 1초 안에 server-to-server로 조회하고 valid enabled policy만 exact `frame-ancestors`로 렌더링한다. 모든 failure는 `'none'`, response는 no-store다. 중첩 frame에서는 모든 ancestor가 목록에 있어야 한다.

### POST /api/v1/run-public/{url_slug}/chat (Public Client-held History)

Request body:

```json
{
  "inputs": {
    "<first_input_variable>": "현재 사용자 메시지"
  },
  "conversation": {
    "history": [
      {"role": "user", "content": "이전 질문"},
      {"role": "assistant", "content": "이전 답변"}
    ]
  }
}
```

- 첫 요청은 `history: []`를 보낸다.
- history item은 정확히 `role`, `content`만 가지며 완료된 `user` → `assistant` pair 순서여야 한다.
- Gateway는 최대 20 turn, message당 32,768자, UTF-8, envelope 131,072 bytes와 현재 inputs를 포함한 4,096-token 상한을 provider 전에 검증한다. Worker는 current inputs의 exact-token 잔여 예산을 다시 만들고, 정제·JSON serialization·untrusted framing이 끝난 최종 history projection도 같은 상한 안으로 가장 오래된 완료 turn부터 제거한다.
- history는 untrusted context로만 사용하고 authorization, system policy와 resource provenance의 근거로 사용하지 않는다.
- `inputs.memory_mode`, `inputs.conversation_id`와 server-side Public Conversation Session은 허용하지 않는다.
- 전용 `/chat` 경로의 성공, validation/router failure와 OPTIONS는 모두 CORS grant 없이 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`를 반환한다. 공용 `/run-public/{url_slug}` root의 Web App/Widget CORS 계약은 변경하지 않는다.

Response: 기존 공개 실행과 동일. `{"status": "success", "results": { ... }}`.

### GET /api/v1/deployments/{deployment_id}/run-info (Current Generic 인증 실행 정보)

Response body:

```json
{
  "deployment_id": "UUID",
  "app_id": "UUID",
  "workflow_id": "UUID",
  "name": "사내 문서 질문 응답 봇",
  "version": 1,
  "description": "선택 설명",
  "type": "internal_chatbot",
  "input_schema": { "variables": [] },
  "output_schema": { "outputs": [] }
}
```

- 실행 화면이 입력 폼과 결과 preview를 만들 때만 사용한다.
- `auth_secret`, `graph_snapshot`, node prompt, KB hidden id 같은 내부 실행 설정은 반환하지 않는다.
- Gateway는 로그인 사용자에게 workflow `execute` 권한이 있는지 재검증한다.
- `X-Organization-Id`가 있으면 해당 active organization scope와 배포 앱의 organization이 일치해야 한다. 불일치 시 404를 반환한다.

### POST /api/v1/deployments/{deployment_id}/run (Current Generic 인증 실행)

Request body:

```json
{
  "inputs": {
    "<first_input_variable>": "사용자 메시지"
  },
  "conversation": {
    "client_id": "3f1c0000-0000-4000-8000-000000000000"
  }
}
```

- Gateway는 로그인 사용자에게 workflow `execute` 권한이 있는지 재검증한다.
- `X-Organization-Id`가 있으면 해당 active organization scope와 배포 앱의 organization이 일치해야 한다. 불일치 시 resource-hiding 정책에 따라 404를 반환한다.
- 서버는 `execution_context.execution_subject = {"type": "user", "id": current_user.id}`를 주입한다.
- LLM node RAG는 이 `execution_subject` 기준으로 Knowledge `use` 권한과 source ACL gate를 다시 평가한다.
- `conversation.client_id`는 UUID이며 extra field를 허용하지 않는다. Chatbot이 아닌 deployment에 전달하면 `400`으로 거부한다.
- 현재 `internal_chatbot` 호환 경로는 서버가 internal memory mode를 강제하므로 신규 내부 Client는 `memory_mode`를 업무 `inputs`에 보내지 않는다. 이 호환 동작은 Public `/chat` 경로에 적용하지 않는다.
- 인증 내부 실행에서 서버는 `deployment_id + execution_subject + client_id`를 domain-separated versioned digest로 바꿔 사용자·배포 간 memory context가 섞이지 않게 한다. raw `client_id`는 dispatch context, 응답, audit와 log에 기록하지 않는다.
- `inputs`에 workflow schema가 선언한 `conversation_id` 또는 `memory_mode`가 있으면 업무 입력으로 보존한다. typed control과 선언되지 않은 legacy `inputs.conversation_id`를 동시에 보내는 모호한 요청은 `400`으로 거부한다.
- 기존 인증 caller의 legacy reserved input은 schema collision이 없는 범위에서만 임시 호환하며 string, 최대 255자, control character 금지 조건을 적용한다. Public 실행에는 이 호환 계약을 적용하지 않는다.
- 요청 `Content-Type`의 media type은 정확히 `application/json`이어야 한다(`charset` parameter 허용). 누락, `text/plain`, `application/x-www-form-urlencoded`, `multipart/form-data`는 body/schema 처리나 workflow dispatch 전에 `415`로 거부한다.
- Browser credentialed JSON 호출은 configured `CORS_ORIGINS`의 명시적 HTTP(S) origin만 preflight를 통과한다. Wildcard credentialed origin은 Gateway 구성 시 거부한다. 이 현행 경계를 별도 CSRF token/exact-Origin 구현 완료로 표현하지 않는다.

Response: `{"status": "success", "results": { ... }}`.

## Authenticated Legacy Compatibility Persistence

- `workflow_runs.conversation_id` (`VARCHAR(255)`, nullable, indexed)는 기존 authenticated compatibility 경로에만 남아 있으며 durable Conversation Session의 source of truth가 아니다. Public `/chat` 요청은 이 필드를 채우지 않는다.

## Memory Scoping

- Public `/chat`은 client history의 각 content를 한 번 정제하고 JSON/framing이 끝난 최종 projection을 exact token으로 다시 bound한다. 이 projection은 현재 provider prompt 앞의 untrusted block과 RAG 검색어 구성에 함께 사용하고 `_build_memory_summary`의 DB 조회를 사용하지 않는다.
- RAG 검색어는 현재 질문을 우선하고 전체 1,000자 안에서 가장 최근 완료 user/assistant pair부터 추가한다. 초과한 오래된 pair는 부분 절단하지 않고 제외한다. Client history는 Knowledge candidate 선택, public/private audience 또는 authorization 판단에 사용하지 않는다.
- authenticated legacy 경로의 `_build_memory_summary`는 기존 호환 범위에만 남는다. Durable internal target은 authenticated session scope, dedicated Memory store, node별 policy와 current source authorization을 사용한다.

## Errors

- 공개 실행과 current generic 인증 실행 모두 404 배포 없음/비활성, 429 예산 초과, 504 타임아웃, 500 엔진 실패를 반환할 수 있다. Public `/chat`은 missing/malformed/oversized/invalid-Unicode history와 legacy control을 content-free `422 conversation.*`로 거부한다. 엔진 실패 응답 detail은 provider 오류, credential, raw payload를 노출하지 않는 고정된 safe message여야 한다. Generic 인증 실행은 추가로 400 invalid/non-object input, invalid/conflicting conversation control, non-Chatbot conversation control, 401/403 인증·권한 오류와 415 non-JSON media type을 반환할 수 있다. Client는 문서화되지 않은 임의 `detail` string을 그대로 표시하지 않는다. Target authenticated internal Chatbot의 별도 permission/error contract는 해당 기능 구현 문서에서 확정한다.

## Citation Response Projection

- public/internal Chatbot run 성공 응답은 기존 결과 field와 함께 optional `__nodease_citations` version 1 sidecar를 포함할 수 있다.
- 공개 Chatbot은 login cookie 존재 여부와 무관하게 anonymous public-only evidence만 Citation으로 투영한다. 내부 Chatbot은 canonical execution subject의 Knowledge 권한과 source ACL을 적용한다.
- Citation sidecar는 conversation memory 원문이나 execution-log payload에 별도 복제하지 않는다.

## Permissions

- 배포 생성은 workflow `deploy` 권한을 요구한다(기존과 동일).
- 공개 실행 표면(`/run-public`)은 무인증이며 `execution_subject`를 주입하지 않는다. 따라서 private Knowledge/RAG는 workflow owner 권한으로 fallback하지 않고 anonymous public-only 후보만 사용할 수 있다.
- `internal_chatbot` 인증 실행(`/deployments/{deployment_id}/run`)은 대상 workflow organization의 active membership과 workflow `execute` 권한을 요구한다. `X-Organization-Id`가 전달되면 배포 앱 organization과의 일치도 확인하며, 로그인 사용자를 RAG 실행 권한 주체로 전달한다.
- Target Conversation Session과 별도 내부 챗봇 access grant는 현재 `internal_chatbot`의 실행 주체·KB permission 재검사를 대체하지 않으며, 도입 시 별도 API 계약으로 추가한다.

## Public Conversation Capability And Rollout

Public Chatbot preflight/create의 `config.public_conversation`은 `public_chat_conversation.v1`과 한 개의 canonical `history_consumer`를 사용한다. 대상 node는 deployment snapshot의 `llmNode`여야 한다. Gateway는 `/chat` 실행 전에 mapping을 검증하고 Worker가 persisted deployment row에서 다시 검증한다.

Public info response의 `public_conversation_contract`는 `client_history_v1` 또는 `legacy_v0`이다. 구 Gateway처럼 필드가 없으면 Client는 legacy로 취급한다. Compatibility rollout에서 legacy root 요청은 새 Gateway가 server Memory 없이 stateless로 실행한다. Strict rollout에서는 root Chatbot 요청과 consumer mapping 없는 legacy deployment의 create/toggle 활성화를 거부하며 toggle 실패는 deployment, App active pointer, schedule과 transaction을 변경하지 않는다.

Public `/chat` raw history는 600초 TTL의 일회성 Redis key에만 저장하고 Celery에는 opaque reference를 전달한다. Execution context는 authorization subject 대신 별도 public audit actor, canonical consumer safe reference, content persistence suppression과 600초 absolute deadline을 포함한다. Celery publish도 같은 시각의 `expires`를 사용한다. Worker는 deadline을 먼저 검사하고 history reference를 atomic GET+DELETE로 소비하며 snapshot에서 consumer ref를 재구성한다. 공통 external-effect executor는 write/read-only provider I/O 전에 같은 monotonic deadline을 검사하고 만료된 claim은 failed-before-effect/stop으로 종료한다.

추가 safe error code는 `conversation.consumer_mapping_required`, `conversation.consumer_mapping_invalid`, `conversation.consumer_mapping_not_found`, `conversation.consumer_mapping_node_type_invalid`, `conversation.request_deadline_invalid`, `conversation.request_expired`다. Request content와 internal node identity는 error에 반사하지 않는다.
