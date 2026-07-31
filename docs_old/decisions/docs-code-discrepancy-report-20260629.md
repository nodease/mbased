# docs 코드 불일치 조사 보고서

Status: Draft
Authority: Decision
Source of Truth: No
Verified Against: dev @ c990b54e931b4de8023822f6dff14f43fc1d415f

작성일: 2026-06-29

범위:
- 문서: `docs/api/*.md`, `docs/architecture/*.md` 중 백엔드/API 관련, `docs/data-model/*.md`, `docs/data-model/diagrams/*.md`
- 코드: `apps/gateway/**`, `apps/shared/**`, `apps/workflow_engine/**`, `tests`

원칙:
- `docs/` 원본과 코드 파일은 수정하지 않았다.
- 아래 내용은 문서를 코드 기준으로 고칠 때 반영할 불일치, 코드 근거, 문장/섹션 방향이다.

## 전체 API 라우트 차이

OpenAPI 생성 결과 기준으로 문서에 누락된 구현 라우트:
- `GET /api/v1/users`
- `GET /api/v1/teams/{team_id}/members`
- `GET /api/v1/workflows/{workflow_id}/permissions/me`
- `POST /api/v1/workflows/{workflow_id}/compare`

문서에는 있으나 구현 라우트가 아닌 항목:
- `GET /api/v1/audit/logs`: `docs/api/tracing-audit.md`에 `Planned`로 표기되어 있으므로 구현 API처럼 옮기지 말고 Planned/roadmap으로 유지한다.

라우터 근거:
- `apps/gateway/api/api.py:31-75`
- `apps/gateway/main.py` OpenAPI 생성 결과

## `docs/api/README.md`

불일치:
- 문서는 인증 API가 session cookie 또는 bearer token을 사용한다고 설명한다.
- 실제 사용자 세션 인증은 `auth_token` cookie만 읽는다. `Authorization: Bearer`는 일반 사용자 세션 인증으로 처리되지 않고, public run/webhook secret 전달에만 쓰인다.

코드 근거:
- `apps/gateway/auth/dependencies.py:18-41`
- `apps/gateway/api/v1/endpoints/auth.py:117-184`, `212-241`
- `apps/gateway/api/v1/endpoints/run.py:12-40`
- `apps/gateway/api/v1/endpoints/webhook.py:21-54`, `97-125`

반영 방향:
- 인증 공통 규칙을 다음처럼 바꾼다: "대부분의 인증된 Gateway API는 `auth_token` HttpOnly cookie를 사용한다. `Authorization: Bearer`는 사용자 세션 인증이 아니라 `/api/v1/run/{url_slug}`와 `/api/v1/hooks/{url_slug}`의 public secret 전달 방식 중 하나다."

불일치:
- 문서는 secret 원문이 API 응답에 노출되지 않는다고 한다.
- 실제 `AppResponse`와 `DeploymentResponse`는 현재 `auth_secret` 원문 필드를 포함한다.

코드 근거:
- `apps/shared/schemas/app.py:32-49`
- `apps/gateway/services/app_service.py:43-65`
- `apps/shared/schemas/deployment.py:27-39`
- `apps/gateway/services/deployment_service.py:165-169`

반영 방향:
- 현재 동작과 목표 원칙을 분리한다: "현재 `AppResponse`와 `DeploymentResponse`에는 `auth_secret`이 포함된다. secret 비노출은 목표 보안 원칙이며, 코드 변경 전까지 문서에는 해당 응답 노출을 명시한다."

## `docs/api/auth.md`

불일치/누락:
- 엔드포인트 목록은 대체로 맞지만, cookie 이름과 설정이 문서에 충분히 명시되어 있지 않다.

코드 근거:
- `apps/gateway/api/v1/endpoints/auth.py:117-133`: signup cookie 설정
- `apps/gateway/api/v1/endpoints/auth.py:168-184`: login cookie 설정
- `apps/gateway/api/v1/endpoints/auth.py:189-209`: logout cookie 삭제
- `apps/gateway/api/v1/endpoints/auth.py:212-241`: `/auth/me` cookie 조회

반영 방향:
- "로그인/회원가입 성공 시 `auth_token` cookie를 설정한다. cookie는 HttpOnly, `path=/`, `max_age=21600`이며 local 환경은 `SameSite=lax`, production은 `SameSite=none; Secure`를 사용한다."
- "`get_current_user` 기반 보호 API는 `Authorization` header가 아니라 `auth_token` cookie를 읽는다."

## `docs/api/organization-rbac.md`

불일치:
- 구현된 `GET /api/v1/teams/{team_id}/members`가 문서 표에 없다.

코드 근거:
- `apps/gateway/api/v1/endpoints/team.py:359-422`
- `apps/shared/schemas/team.py:32-37`

반영 방향:
- Team API 표에 다음 행을 추가한다: "`GET /api/v1/teams/{team_id}/members`는 `X-Organization-Id`와 organization `manager` 권한이 필요하며, active organization 안의 active team member 목록을 `TeamMemberResponse[]`로 반환한다."

불일치:
- 구현된 `GET /api/v1/users`가 문서 표에 없다.

코드 근거:
- `apps/gateway/api/v1/endpoints/users.py:22-63`
- `apps/shared/schemas/auth.py:23-32`

반영 방향:
- Organization/RBAC 보조 API로 다음 섹션을 추가한다: "`GET /api/v1/users`는 organization manager 전용 사용자 검색 API다. Query는 `organization_id?`, `q?`, `limit=50(1..200)`를 받으며, active team membership이 있는 비활성화되지 않은 사용자를 `UserResponse[]`로 반환한다."

불일치/누락:
- `GET /api/v1/teams`의 `limit` query가 문서에 없다.

코드 근거:
- `apps/gateway/api/v1/endpoints/team.py:233-267`

반영 방향:
- "`GET /api/v1/teams`는 `limit` query를 받으며 기본값은 문자열 `"10"`이고 실제 파싱 후 1..100 범위로 제한한다."

## `docs/api/apps-workflows.md`

불일치:
- `GET /api/v1/apps/explore`가 public/explore read로 문서화되어 있으나 실제로는 로그인 사용자만 접근한다.

코드 근거:
- `apps/gateway/api/v1/endpoints/app.py:82-93`

반영 방향:
- 권한 칼럼을 "authenticated user"로 바꾸고, "explore 목록은 공개 앱 목록이지만 현재 구현은 `auth_token` cookie 인증을 요구한다"를 적는다.

불일치:
- 구현된 `GET /api/v1/workflows/{workflow_id}/permissions/me`가 문서에 없다.

코드 근거:
- `apps/gateway/api/v1/endpoints/workflow.py:591-615`
- `apps/shared/services/permissions.py:184-232`

반영 방향:
- Workflow API 표에 다음 행을 추가한다: "`GET /api/v1/workflows/{workflow_id}/permissions/me`는 workflow `read` 권한이 필요하며, 현재 사용자의 effective `auth_state`와 `can_read`, `can_write`, `can_execute`, `can_deploy`, `can_manage` booleans를 반환한다."

불일치:
- 구현된 `POST /api/v1/workflows/{workflow_id}/compare`가 문서에 없다.

코드 근거:
- `apps/gateway/api/v1/endpoints/workflow.py:52-57`
- `apps/gateway/api/v1/endpoints/workflow.py:691-772`

반영 방향:
- Workflow API 표에 다음 행을 추가한다: "`POST /api/v1/workflows/{workflow_id}/compare`는 workflow `execute` 권한이 필요하다. Body는 `node_id`, `compare_type: model|prompt`, `inputs`, `left`, `right`이며, A/B variant 실행 결과를 반환한다."

불일치:
- App 응답 보안 설명이 `auth_secret` 비노출 원칙만 말하면 실제 schema와 다르다.

코드 근거:
- `apps/shared/schemas/app.py:32-49`

반영 방향:
- AppResponse 필드 표에 `auth_secret`이 현재 포함됨을 명시하고, masking/제거는 목표 또는 보안 TODO로 분리한다.

## `docs/api/deployments.md`

불일치/누락:
- public run/webhook secret 전달 방식과 상태코드가 문서보다 구체적이다.

코드 근거:
- `apps/gateway/api/v1/endpoints/run.py:12-40`
- `apps/gateway/services/deployment_service.py:360-371`
- `apps/gateway/api/v1/endpoints/webhook.py:21-54`, `97-125`

반영 방향:
- "`POST /api/v1/run/{url_slug}`는 `Authorization: Bearer <auth_secret>` 또는 `X-Auth-Secret`을 받으며 secret 불일치 시 401을 반환한다."
- "`POST /api/v1/hooks/{url_slug}`는 `?token=`, `Authorization: Bearer`, `X-Webhook-Secret` 중 하나를 받으며 secret 불일치 시 403을 반환한다."

불일치:
- `DeploymentResponse.auth_secret`은 문서의 비노출 원칙과 달리 현재 응답에 포함된다.

코드 근거:
- `apps/shared/schemas/deployment.py:27-39`
- `apps/gateway/services/deployment_service.py:165-169`

반영 방향:
- "`auth_secret`은 현재 deployment 응답에 포함된다. 원문 비노출은 목표 보안 원칙이며, masking 또는 제거 전까지 current behavior로 문서화한다."

불일치/누락:
- `GET /api/v1/deployments`의 query 동작이 문서에 부족하다.

코드 근거:
- `apps/gateway/api/v1/endpoints/deployment.py:107-150`
- `apps/gateway/tests/api/test_deployment_permissions.py:29-119`

반영 방향:
- "`GET /api/v1/deployments`는 `app_id?`, `workflow_id?`, `skip=0`, `limit=100`을 받는다. `app_id`와 `workflow_id`가 모두 없으면 빈 배열을 반환하고, 둘 다 있되 서로 다른 workflow를 가리키면 app workflow read 권한 확인 후 400을 반환한다."

## `docs/api/tracing-audit.md`

불일치/누락:
- tracing query parameter와 기본값이 문서에 충분히 적혀 있지 않다.

코드 근거:
- `apps/gateway/api/v1/endpoints/tracing.py:84-115`
- `apps/gateway/api/v1/endpoints/tracing.py:120-137`
- `apps/gateway/api/v1/endpoints/tracing.py:154-177`

반영 방향:
- `GET /api/v1/traces`: `status`, `trigger_mode`, `from`, `to`, `app_id`, `workflow_id`, `deployment_id`, `user_id`, `correlation_id`, `page=1`, `limit=20(1..100)`를 명시한다.
- `GET /api/v1/traces/{trace_id}`: `view=metadata`, `include_spans=false`, `include_payloads=false`를 명시한다.
- `GET /api/v1/traces/{trace_id}/payloads`: `view=redacted`, `payload_kind?`, `node_run_id?`, `history=false`, `page=1`, `limit=1000(1..1000)`를 명시한다.

불일치:
- raw payload 접근이 `raw_auditor` 또는 `manager` 중심으로만 문서화되어 있으나, 실제 구현은 trace visibility policy와 system admin/app owner/workflow RBAC를 조합한다.

코드 근거:
- `apps/shared/services/tracing/access.py:151-180`
- `apps/shared/services/tracing/access.py:259-360`
- `apps/shared/services/tracing/access.py:372-447`
- `apps/shared/services/tracing/rbac.py:54-79`

반영 방향:
- "Trace 접근은 system admin, app owner, workflow effective auth_state, trace visibility policy를 조합해 판정한다. 일반 RBAC 사용자는 metadata는 `viewer` 이상, redacted payload는 `builder` 이상, raw payload는 `manager` 이상이어야 하며, visibility policy가 각 view를 허용해야 한다."

불일치/누락:
- raw payload 조회 시도는 별도 audit table에 기록된다.

코드 근거:
- `apps/shared/services/tracing/access.py:449-491`

반영 방향:
- "raw view 요청은 허용/거부 여부와 reason_code를 `trace_payload_access_events`에 먼저 기록한다. 기록 실패 시 strict mode에서는 raw 응답을 진행하지 않는다."

불일치:
- schema에는 `scope_type="organization"`이 있으나 policy 관리 API 서비스는 organization scope를 거부한다.

코드 근거:
- `apps/shared/schemas/tracing.py:7-9`, `100-139`
- `apps/shared/services/tracing/policy.py:201-215`

반영 방향:
- "Tracing policy 관리 API는 현재 `global`과 `app` scope만 허용한다. `organization` scope는 schema type에는 남아 있으나 서비스에서 `organization_scope_policy_not_supported`로 거부된다."

불일치:
- audit search는 구현 API가 `/api/v1/users/me/audit-logs`뿐이고, `/api/v1/audit/logs`는 아직 Planned다.

코드 근거:
- `apps/gateway/api/v1/endpoints/users.py:66-109`

반영 방향:
- "현재 감사 로그 조회 구현은 본인 actor 로그만 반환하는 `GET /api/v1/users/me/audit-logs`다. `GET /api/v1/audit/logs`는 organization/audit 권한 기반 검색 API로 Planned 상태를 유지한다."

## `docs/api/llm-credentials.md`

불일치:
- 문서는 `api_key`를 저장 전 암호화해야 한다고 하지만 실제 `register_credential`은 원문 API key를 JSON 문자열로 `encrypted_config`에 저장한다.

코드 근거:
- `apps/gateway/services/llm_service.py:566-588`
- `apps/shared/db/models/llm.py:176-182`

반영 방향:
- "`encrypted_config` 컬럼명과 달리 현재 구현은 `{"apiKey": "...", "baseUrl": "..."}` JSON 문자열을 저장하고, `config_preview`만 마스킹한다. 암호화 저장은 보안 개선 TODO로 분리한다."

불일치/누락:
- `LLMCredentialResponse` 필드가 문서보다 많다.

코드 근거:
- `apps/shared/schemas/llm.py:55-72`

반영 방향:
- 응답 필드에 `organization_id`, `created_at`, `updated_at`을 추가한다.

불일치/누락:
- `POST /api/v1/llm/credentials/{credential_id}/sync-models`는 query parameter `purge_unverified=false`를 받는다.

코드 근거:
- `apps/gateway/api/v1/endpoints/llm.py:136-150`

반영 방향:
- "Query: `purge_unverified` boolean, default `false`"를 API 표나 endpoint detail에 추가한다.

불일치/누락:
- system/provider/pricing 권한 설명을 더 정확히 해야 한다.

코드 근거:
- `apps/gateway/api/v1/endpoints/llm.py:29-45`
- `apps/gateway/api/v1/endpoints/llm.py:219-252`
- `apps/gateway/services/llm_service.py:521-535`
- `apps/gateway/tests/services/test_llm_service_permissions.py`

반영 방향:
- "`GET /llm/providers`는 system catalog 조회지만 로그인 인증은 필요하다."
- "`POST /llm/models/sync-pricing`와 `PUT /llm/models/{model_id}/pricing`은 `TraceAccessService.is_system_admin` 통과가 필요하다."
- "`GET /llm/credentials`는 is_valid credential 중 현재 사용자가 LLM credential `read` 권한을 가진 항목만 반환한다."

## `docs/api/errors.md`

불일치/누락:
- `X-Request-ID` 처리와 실제 error envelope 혼용 규칙이 문서에 부족하다.

코드 근거:
- `apps/gateway/main.py:58-76`
- `apps/gateway/main.py:78-107`
- `apps/gateway/main.py:110-122`
- `apps/gateway/utils/api_errors.py:8-68`
- `apps/gateway/tests/api/test_request_id_middleware.py:14-22`

반영 방향:
- "모든 HTTP 응답은 요청의 `X-Request-ID`를 보존하거나 없으면 UUID를 생성해 응답 header `X-Request-ID`에 넣는다."
- "일반 `HTTPException`은 기본적으로 `{"detail": ...}` 형태로 반환된다. `api_errors.raise_api_error`로 올린 예외와 validation error는 `{"error": {...}}` envelope를 반환한다."
- "401/403 `HTTPException`은 `audit_recorded`가 없으면 permission denied audit가 기록된다."

## `docs/api/knowledge-rag.md`

핵심 불일치:
- 조사 범위의 구현 endpoint 목록과 주요 request body는 문서와 대체로 맞다.

확인 근거:
- `apps/gateway/api/v1/endpoints/knowledge.py:494-577`: process는 `DocumentPreviewRequest` body를 받고 202를 반환한다.
- `apps/gateway/api/v1/endpoints/knowledge.py:580-639`: preview는 `DocumentPreviewRequest` body를 받고 `DocumentPreviewResponse`를 반환한다.

반영 방향:
- 고위험 수정 항목은 없다. 필요하면 process/sync 응답이 `{status, message}`인 점만 endpoint detail에 보강한다.

## `docs/api/supporting-endpoints.md`

불일치/누락:
- `POST /api/v1/connectors/test`는 인증 의존성이 없는 연결 테스트 API다. 저장/조회/schema API는 current user owner scope를 사용한다.

코드 근거:
- `apps/gateway/api/v1/endpoints/connectors.py:37-87`
- `apps/gateway/api/v1/endpoints/connectors.py:89-185`
- `apps/gateway/api/v1/endpoints/connectors.py:188-242`

반영 방향:
- "연결 테스트(`/connectors/test`)는 현재 인증 의존성이 없다. 연결 저장, 상세 조회, schema 조회는 `auth_token` 인증과 connection owner check를 사용한다."

## `docs/architecture/README.md`

불일치:
- 문서는 controller가 비즈니스 로직과 권한 판단 DB 상세 조회를 소유하지 않는다고 하지만, 현재 Gateway endpoint 일부는 endpoint-local helper와 DB query로 권한/스코프를 판정한다.

코드 근거:
- `apps/gateway/api/v1/endpoints/team.py:233-422`
- `apps/gateway/api/v1/endpoints/permissions.py:1221-1715`
- `apps/gateway/api/v1/endpoints/users.py:22-109`
- `apps/gateway/services/organization_context.py:34-51`

반영 방향:
- 현재/목표를 분리한다: "목표 아키텍처는 controller thinness와 service/helper 권한 재사용이지만, 현재 일부 organization/team/permission endpoint는 controller-local helper와 DB query를 포함한다. 신규 구현은 service/helper로 이동하는 방향을 따른다."

## `docs/architecture/auth-rbac.md`

불일치:
- controller가 resource permission 비즈니스 규칙을 소유하지 않는다는 설명이 현재 구현과 다르다.

코드 근거:
- `apps/gateway/api/v1/endpoints/permissions.py:1221-1715`
- `apps/shared/services/permissions.py:184-232`
- `apps/gateway/auth/permissions.py:54-126`

반영 방향:
- "워크플로우/LLM effective permission 판정은 `apps/shared/services/permissions.py`와 `apps/gateway/auth/permissions.py`에 모여 있지만, permission management API의 grant/list/revoke 경로는 endpoint-local authorization helper와 DB query를 사용한다."

## `docs/architecture/system-overview.md`

불일치:
- secret이 API 응답에 반환되지 않는다는 절대 문구는 현재 `auth_secret` 응답 노출과 충돌한다.

코드 근거:
- `apps/shared/schemas/app.py:32-49`
- `apps/shared/schemas/deployment.py:27-39`
- `apps/gateway/services/deployment_service.py:165-169`

반영 방향:
- "Secret 비노출은 목표 보안 원칙이다. 현재 예외로 app/deployment 응답에 `auth_secret`이 포함되며, masking 또는 제거 전까지 current behavior로 문서화한다."

## `docs/architecture/tracing-audit.md`

불일치/누락:
- trace access architecture 설명에 실제 판정 계층과 raw access audit 순서가 부족하다.

코드 근거:
- `apps/shared/services/tracing/access.py:151-180`
- `apps/shared/services/tracing/access.py:259-360`
- `apps/shared/services/tracing/access.py:372-491`
- `apps/shared/services/tracing/rbac.py:54-79`

반영 방향:
- "Trace access layer는 run에서 app/workflow/organization context를 해석한 뒤 visibility policy, system admin, app owner, workflow RBAC auth_state를 조합한다."
- "Raw payload access는 응답 전에 `trace_payload_access_events`에 allowed/denied event를 기록한다."

## `docs/data-model/physical-data-model.md`

불일치:
- `llm_credentials.encrypted_config`는 물리 컬럼명과 달리 실제 서비스에서 암호화되지 않은 JSON 문자열이 들어간다.

코드 근거:
- `apps/shared/db/models/llm.py:176-182`
- `apps/gateway/services/llm_service.py:577-588`

반영 방향:
- "`encrypted_config`는 현재 plain JSON 문자열을 저장한다. 컬럼명은 유지되어 있지만 실제 암호화 저장은 미구현 보안 TODO다."

불일치/누락:
- `auth_state` 제약 설명이 테이블별로 다르다.

코드 근거:
- `apps/shared/db/models/team.py:160-188`
- `apps/shared/db/models/team.py:306-370`

반영 방향:
- "Team resource permission tables의 `auth_state`는 문자열 컬럼이고 DB check constraint가 없다. Direct user permission tables인 `user_workflow_permissions`, `user_llm_permissions`에는 canonical state check constraint가 있다."

## `docs/data-model/rbac-permission-policy.md`

불일치:
- audit/trace enforcement matrix가 `team_audit_permissions` 중심 current behavior처럼 보이지만, 현재 구현된 audit 조회는 본인 audit log 조회뿐이고 trace raw access는 workflow RBAC/app owner/system admin/visibility policy 중심이다.

코드 근거:
- `apps/gateway/api/v1/endpoints/users.py:66-109`
- `apps/shared/services/tracing/access.py:151-180`
- `apps/shared/services/tracing/access.py:372-447`
- `apps/shared/services/tracing/rbac.py:54-79`

반영 방향:
- "현재 구현: audit log 조회는 `GET /api/v1/users/me/audit-logs`의 self-scope만 있다. `team_audit_permissions` 기반 organization audit search는 구현 route가 없고 Planned다."
- "현재 trace raw payload 조회는 audit auth_state(`raw_auditor`)가 아니라 workflow effective auth_state(`manager` 이상 for regular RBAC), app owner/system admin 여부, trace visibility policy로 판정한다."
- "목표 모델로 `team_audit_permissions`/future `user_audit_permissions`를 유지하되 current enforcement와 target enforcement를 분리해서 적는다."

## `docs/data-model/diagrams/*.md`

핵심 불일치:
- 물리 관계 자체는 코드 table 목록과 대체로 일치한다.
- 단, `team_audit_permissions`가 current audit search/trace raw access enforcement에 직접 쓰이는 것처럼 해석될 수 있으면 주석 보강이 필요하다.

코드 근거:
- `apps/shared/db/models/team.py`
- `apps/gateway/api/v1/endpoints/users.py:66-109`
- `apps/shared/services/tracing/access.py:151-180`

반영 방향:
- diagram 자체는 필수 수정 대상이 아니다. 필요하면 `team_audit_permissions` 옆에 "planned/currently not wired to organization audit search route" 메모를 추가한다.

## 우선순위

1. 보안 관련 문구 정정: `auth_secret` 응답 노출, LLM `encrypted_config` 실제 비암호화 저장, bearer token 범위.
2. 누락 구현 API 추가: `GET /users`, `GET /teams/{team_id}/members`, `GET /workflows/{workflow_id}/permissions/me`, `POST /workflows/{workflow_id}/compare`.
3. tracing/audit current vs target 분리: self audit logs, planned audit search, trace access policy.
4. architecture 문구 정정: controller thinness는 목표, 일부 endpoint-local DB 권한 판단은 현재 예외.
5. 데이터 모델 보강: permission auth_state DB constraint 차이, audit permission target/current 분리.
