# App 및 Workflow API

Status: Draft
Authority: API
Source of Truth: Yes
Verified Against: feature/mba-74 @ PR #131 head
Related ADRs: [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md), [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202606291315-resource-access-403-404-policy](../decisions/ADR-202606291315-resource-access-403-404-policy.md)

## 범위

App/project boundary, workflow CRUD, draft, execute, stream, run detail 계약을 정의한다.

## App 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/apps` | `AppCreateRequest` | `AppResponse` | authenticated + `X-Organization-Id` active organization scope |
| Implemented | `GET` | `/api/v1/apps` | query | `AppResponse[]` | `X-Organization-Id` active organization scope + app read |
| Implemented | `GET` | `/api/v1/apps/operations` | query | `AppOperationRow[]` | `X-Organization-Id` active organization scope + operations read |
| Implemented | `GET` | `/api/v1/apps/explore` | query | `AppResponse[]` | authenticated explore read |
| Implemented | `GET` | `/api/v1/apps/{app_id}` | 없음 | `AppResponse` | app read |
| Implemented | `PATCH` | `/api/v1/apps/{app_id}` | `AppUpdateRequest` | `AppResponse` | app settings/manage |
| Implemented | `POST` | `/api/v1/apps/{app_id}/clone` | 없음 | `AppResponse` | app read + `X-Organization-Id` active organization scope for target app |
| Implemented | `DELETE` | `/api/v1/apps/{app_id}` | 없음 | message | app manage |

App 전용 permission table은 만들지 않는다. App read/settings 권한은 organization owner/manager 또는 primary workflow 권한으로 판정한다.
App 생성과 clone으로 생성되는 primary workflow에는 생성자 user direct `manager` 권한을 부여한다.

현재 `AppResponse`에는 `url_slug`와 `auth_secret`이 포함된다. `auth_secret` 원문 비노출은 목표 보안 원칙이며, 현재 schema와 서비스가 masking/removal을 적용하기 전까지 app 조회/생성/복제 응답에서 노출될 수 있다.

현재 backend contract에서 `POST /apps`, `GET /apps`, `POST /apps/{app_id}/clone`, `POST /workflows`는 `X-Organization-Id` header를 요구한다. Header가 없으면 `400 organization.required`가 발생한다. MBA-71 프론트 변경 기준 `appApi`는 공통 `apiClient`를 통해 active organization header를 자동 첨부한다. `workflowApi`와 `webhookApi`처럼 별도 axios instance를 쓰는 wrapper는 `attachActiveOrganizationHeader` helper로 같은 header 정책을 적용한다.

### `GET /api/v1/apps/operations`

MBA-76에서 추가한 API다. `/dashboard/mymodule`이 app 목록, workflow effective permission, 권한 출처, 배포 상태, 최근 run 상태를 한 화면에서 비교할 수 있도록 화면 단위 summary를 반환한다.

이 endpoint는 `GET /api/v1/apps`를 대체하는 범용 app list가 아니다. 내 모듈 운영 목록 화면에 필요한 safe summary만 반환한다.

#### Request

| Query | Type | Required | 설명 |
| --- | --- | --- | --- |
| `q` | string | No | app 이름/설명 부분 검색 |
| `permission` | string | No | `viewer`, `operator`, `builder`, `manager` 등 effective workflow `auth_state` 필터. Capability filter가 아니라 auth state filter다. |
| `capability` | string | No | `execute`, `write`, `manage` 중 하나. `permission.can_execute/write/manage` 기준 capability 필터다. |
| `deployment_state` | string | No | `active`, `inactive`, `undeployed` |
| `run_state` | string | No | `running`, `success`, `failed`, `not_started`, `unavailable` |
| `limit` | integer | No | 기본 `50`, 허용 범위 `1..100` |
| `offset` | integer | No | 기본 `0` |

현재 구현은 `q`, `permission`, `capability`, `deployment_state`, `run_state`, `limit`, `offset`을 지원한다.

여러 filter query를 동시에 전달하면 AND 조건으로 적용한다. 예를 들어 `capability=write&deployment_state=active`는 워크플로우 수정 권한이 있고 배포 중인 row만 반환한다.

`q`는 app 이름/설명의 부분 문자열 검색이다. `%`, `_`, `\`는 SQL wildcard가 아니라 literal 문자로 취급한다.

#### Response

```json
[
  {
    "app": {
      "id": "uuid",
      "name": "고객 문의 분류",
      "description": "문의 내용을 분류하고 담당 팀을 추천합니다.",
      "icon": { "type": "emoji", "content": "📨", "background_color": "#E0F2FE" },
      "workflow_id": "uuid",
      "owner_name": "Admin User",
      "created_at": "2026-06-29T00:00:00Z",
      "updated_at": "2026-06-29T00:00:00Z"
    },
    "permission": {
      "workflow_id": "uuid",
      "organization_id": "uuid",
      "auth_state": "builder",
      "can_read": true,
      "can_write": true,
      "can_execute": true,
      "can_deploy": false,
      "can_manage": false
    },
    "permission_status": "loaded",
    "permission_sources": [],
    "deployment": {
      "state": "active",
      "deployment_id": "uuid",
      "type": "webhook",
      "is_active": true
    },
    "latest_run": {
      "state": "success",
      "run_id": "uuid",
      "raw_status": "success",
      "started_at": "2026-06-29T00:00:00Z",
      "finished_at": "2026-06-29T00:00:05Z",
      "error_message": null
    }
  }
]
```

#### Field contract

##### `AppOperationAppSummary`

| Field | Type | 설명 |
| --- | --- | --- |
| `id` | UUID | app id |
| `name` | string | app 이름 |
| `description` | string \| null | app 설명 |
| `icon` | `AppIcon` \| null | 목록 표시용 icon |
| `workflow_id` | UUID \| null | primary workflow id |
| `owner_name` | string \| null | 생성자 표시 이름. 사용자 이름이 없으면 email 또는 `null` |
| `created_at` / `updated_at` | datetime | app 생성/수정 시각 |

`AppOperationAppSummary`는 `AppResponse` 전체를 그대로 반환하지 않는다. `url_slug`, `auth_secret`처럼 운영 목록에 필요 없거나 secret 성격이 있는 필드는 반환하지 않는다.

##### `AppOperationPermissionSummary`

`permission`은 `GET /api/v1/workflows/{workflow_id}/permissions/me`의 effective permission payload와 같은 action boolean 의미를 사용한다.

| Field | Type | 설명 |
| --- | --- | --- |
| `workflow_id` | UUID | workflow id |
| `organization_id` | UUID \| null | workflow organization id |
| `auth_state` | string | `none`, `viewer`, `operator`, `builder`, `manager` |
| `can_read` / `can_write` / `can_execute` / `can_deploy` / `can_manage` | boolean | action 허용 여부 |

`permission_status`는 row별 permission 계산 상태다.

| Value | 의미 |
| --- | --- |
| `loaded` | permission 계산 성공 |
| `failed` | permission 계산 실패. 이 경우 `permission_error`를 포함할 수 있다. |
| `not_available` | 연결된 workflow가 없어 계산 대상이 없음 |

현재 구현은 permission 계산 예외를 row 단위로 숨기지 않는다. `failed`와 `permission_error`는 향후 row-level partial failure를 도입할 때를 위한 예약 상태다.

`permission_sources`는 MBA-74 `GET /api/v1/workflows/{workflow_id}/permissions/me`의 `sources` 계약과 동일한 source schema를 사용한다.

##### `AppOperationDeploymentSummary`

| Field | Type | 설명 |
| --- | --- | --- |
| `state` | string | `active`, `inactive`, `undeployed` |
| `deployment_id` | UUID \| null | `active`이면 active deployment id. `inactive`이면 최근 deployment id를 반환할 수 있고, 이력 join을 하지 않는 초기 구현에서는 `null` 가능. `undeployed`이면 `null` |
| `type` | string \| null | deployment type. `undeployed`이면 `null` |
| `is_active` | boolean \| null | deployment 활성 여부. `undeployed`이면 `null` |

`state` 계산 기준:

- `app.active_deployment_id`가 있고 연결된 deployment `is_active == true`이면 `active`
- `app.active_deployment_id is null`이고 workflow/app에 deployment 이력이 있으면 `inactive`
- deployment 이력이 없으면 `undeployed`

현재 구현은 deployment 이력을 조회해 `inactive`를 계산한다.

##### `AppOperationLatestRunSummary`

| Field | Type | 설명 |
| --- | --- | --- |
| `state` | string | `running`, `success`, `failed`, `not_started`, `unavailable` |
| `run_id` | UUID \| null | 최신 run id |
| `raw_status` | string \| null | DB/API 원본 run status |
| `started_at` / `finished_at` | datetime \| null | 최신 run 시작/종료 시각 |
| `error_message` | string \| null | 실패 사유 요약 |

`error_message`는 raw payload를 포함하지 않는 요약 메시지만 반환한다. secret, token, credential, prompt/completion 원문은 포함하지 않으며 필요 시 redacted string 또는 `null`로 처리한다.

`latest_run`은 현재 user가 해당 workflow `read` 권한을 가진 row에 대해서만 계산한다. 권한 없는 app/workflow의 run 정보는 row 자체를 반환하지 않는다.

상태 계산 기준:

| Source | `state` |
| --- | --- |
| 최신 run 없음 | `not_started` |
| `running` | `running` |
| `success` | `success` |
| `failed`, `stopped` | `failed` |
| 상태 계산 실패 또는 source unavailable | `unavailable` |

현재 DB `workflow_runs.status` 기준 source 값은 `running`, `success`, `failed`, `stopped`다. 대문자 또는 `pending`, `queued`, `in_progress`, `completed`, `error` 같은 legacy/외부 상태값은 방어적 normalize 대상으로만 처리한다.

#### Permission and scope

- `X-Organization-Id`는 필수다. Header가 없으면 `400 organization.required`를 반환한다.
- active organization scope 밖이면 `404 resource.not_found`로 숨긴다.
- 응답 row는 현재 user가 operations summary 노출 판정을 통과한 app만 포함한다. 이 문서의 `operations read`는 독립 RBAC action이 아니라 organization manager 또는 primary workflow `read` 기반의 화면 전용 노출 판정이다.
- organization manager는 active organization 안의 app을 볼 수 있다.
- 일반 member는 workflow effective `read` 이상 권한이 있는 app만 볼 수 있다.
- Marketplace/public app read는 operations read로 간주하지 않는다. Public app이더라도 organization manager 또는 primary workflow read 권한이 없으면 deployment/latest run summary를 반환하지 않는다.
- run/deployment join 과정에서 권한 없는 workflow, 다른 organization workflow, inactive organization resource가 누출되면 안 된다.
- partial failure가 발생해도 권한 없는 resource를 placeholder row로 반환하지 않는다. 권한 계산 실패가 같은 scope 안의 내부 오류이면 해당 row의 `permission_status="failed"`와 `permission_error`를 사용할 수 있다.

#### Implementation notes

- 기본 정렬은 `app.updated_at desc`, `app.name asc`, `app.id asc`를 사용한다. `app.id`는 offset pagination의 stable tie-breaker다.
- FastAPI route는 `/apps/{app_id}`보다 `/apps/operations`를 먼저 등록해야 한다. 그렇지 않으면 `operations`가 `app_id` path param으로 해석될 수 있다.
- 최신 run은 `workflow_runs.started_at desc` 기준 1건을 사용한다.
- App, owner, active deployment, latest run, deployment history 조회는 service-level aggregation으로 묶는다. Effective permission 계산은 현재 workflow permission helper를 사용한다.
- app 후보 조회, operations read 권한 판정, 계산된 summary filter는 query batch를 순회하며 필요한 page를 채울 때까지만 수행한다.
- 권한 출처는 MBA-74와 같은 `team`/`user` source schema를 사용한다. 같은 source schema를 두 endpoint에서 중복 정의하지 말고 shared schema로 분리하는 것을 권장한다.
- 새로운 aggregate table은 만들지 않는다. MVP 기준 source of truth는 `apps`, `workflows`, `workflow_deployments`, `workflow_runs`, `team_workflow_permissions`, `user_workflow_permissions`, `organization_memberships`, `team_memberships`다.

## Workflow 엔드포인트

| Status | Method | Path | Request | Response | Permission |
| --- | --- | --- | --- | --- | --- |
| Implemented | `POST` | `/api/v1/workflows` | `WorkflowCreateRequest` | `WorkflowResponse` | `X-Organization-Id` active organization scope + app manage; workflow inherits app organization |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}` | 없음 | `WorkflowResponse` | workflow `read` |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/permissions/me` | 없음 | `WorkflowPermissionResponse` | workflow `read` |
| Implemented | `GET` | `/api/v1/workflows/app/{app_id}` | 없음 | `WorkflowResponse[]` | app read |
| Implemented | `POST` | `/api/v1/workflows/{workflow_id}/draft` | `WorkflowDraftRequest` | message | workflow `write` |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/draft` | 없음 | draft graph | workflow `read` |
| Implemented | `POST` | `/api/v1/workflows/{workflow_id}/compare` | `WorkflowCompareRequest` | A/B variant result | workflow `execute` |
| Implemented | `POST` | `/api/v1/workflows/{workflow_id}/execute` | execution input | run result | workflow `execute` |
| Implemented | `POST` | `/api/v1/workflows/{workflow_id}/stream` | form/input | `text/event-stream` | workflow `execute` |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/runs` | pagination query | `WorkflowRunListResponse` | workflow `read` |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/runs/{run_id}` | 없음 | `WorkflowRunSchema` | workflow `read` |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/runs/{run_id}/llm-traces` | `node_id`, `limit`, `offset` query | `LLMTraceListResponse` | workflow `read` |
| Implemented | `GET` | `/api/v1/workflows/{workflow_id}/stats` | query | `DashboardStatsResponse` | workflow `read` |

## 주요 스키마

### `AppCreateRequest`

| Field | Type | Required |
| --- | --- | --- |
| `name` | string | Yes |
| `description` | string | No |
| `icon` | `AppIcon` | Yes |
| `is_market` | boolean | No |

### `WorkflowDraftRequest`

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `nodes` | `NodeSchema[]` | No | canvas node 목록 |
| `edges` | `EdgeSchema[]` | No | canvas edge 목록 |
| `viewport` | `ViewportSchema` | No | canvas viewport |
| `features` | object | No | workflow feature flags |
| `envVariables` | array | No | 환경 변수 |
| `runtimeVariables` | array | No | 실행 시 입력 변수 |

### `WorkflowCompareRequest`

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `node_id` | string | Yes | 비교 대상 LLM node id |
| `compare_type` | `model` 또는 `prompt` | Yes | `model_id`를 바꿀지 `user_prompt`를 바꿀지 결정 |
| `inputs` | object | No | 실행 입력값 |
| `left` | string | Yes | A variant 값 |
| `right` | string | Yes | B variant 값 |

## 실행 입력 규칙

- `POST /workflows/{workflow_id}/execute`는 저장된 draft graph를 사용하고, JSON body의 `memory_mode` 값은 실행 입력에서 제거한 뒤 execution context에만 전달한다.
- Client는 SSE buffering을 피하기 위해 `/stream-api/workflows/{workflowId}` Next.js route로 요청하고, 이 route가 backend `/api/v1/workflows/{workflow_id}/stream`으로 프록시한다.
- Backend `POST /workflows/{workflow_id}/stream`은 JSON body 또는 `multipart/form-data`를 모두 지원한다.
- stream JSON body는 `{ "inputs": object, "graph_snapshot": object }` 형태를 지원하며, `graph_snapshot`이 있으면 저장된 draft 대신 그 snapshot을 실행한다.
- stream `multipart/form-data`는 `inputs` JSON 문자열, `graph_snapshot` JSON 문자열, `memory_mode`, `file_변수명` 업로드를 받을 수 있다.
- 실행 graph 검증은 없는 node를 참조하는 edge, trigger/source-only node로 들어오는 edge, answer/terminal node에서 나가는 edge, 순환 연결을 `400`으로 거부한다.

## Effective Permission 응답

`GET /api/v1/workflows/{workflow_id}/permissions/me`는 현재 user의 effective workflow `auth_state`, action별 boolean, 권한 출처 source 목록을 반환한다.

| Field | 설명 |
| --- | --- |
| `workflow_id` | workflow id |
| `organization_id` | workflow organization id. legacy workflow면 `null` 가능 |
| `auth_state` | `none`, `viewer`, `operator`, `builder`, `manager` 중 effective 상태 |
| `can_read` / `can_write` / `can_execute` / `can_deploy` / `can_manage` | 현재 상태가 각 action을 허용하는지 |
| `sources` | 현재 user가 해당 workflow에 접근할 수 있게 한 team/user direct grant 출처 목록 |

`sources`는 effective permission 계산에 사용된 resource-level grant의 근거를 설명하기 위한 목록이다. 기존 `auth_state`와 `can_*`는 그대로 유지한다.

`sources`는 다음 schema를 사용한다.

| Field | Type | Required | 설명 |
| --- | --- | --- | --- |
| `type` | `"team"` 또는 `"user"` | Yes | 권한 출처 종류 |
| `team_id` | UUID | `type="team"`일 때 Yes | 권한을 부여한 team id |
| `team_name` | string | `type="team"`일 때 Yes | 권한을 부여한 team 이름 |
| `user_id` | UUID | `type="user"`일 때 Yes | 직접 권한을 받은 user id |
| `user_name` | string \| null | `type="user"`일 때 No | 백엔드가 만든 user 표시 문자열. user 이름이 없으면 email을 fallback으로 넣을 수 있다. |
| `auth_state` | string | Yes | 해당 source row의 workflow auth_state |

API 응답은 source type에 맞지 않는 sibling field를 생략한다. 예를 들어 team source에는 `user_id`, `user_name`을 내려주지 않고, user source에는 `team_id`, `team_name`을 내려주지 않는다.

반환 규칙:

- team source는 active team, 현재 user의 active team membership, 같은 organization scope, 같은 `grantee_organization_id`, 같은 workflow scope를 만족하는 `team_workflow_permissions` row에서 만든다.
- user source는 현재 user에게 직접 부여된 `user_workflow_permissions` row에서 만든다.
- 복수 source가 있으면 모두 반환한다. effective `auth_state`는 기존 정책대로 source들 중 가장 강한 권한과 organization manager override를 반영한다.
- user direct permission은 additive allow다. 더 약한 user direct source가 있어도 더 강한 team source를 낮추지 않는다.
- organization manager override는 `auth_state=manager`로 반영되지만 MBA-74의 `sources`에는 team/user direct source만 포함한다. team/user direct source가 없으면 `sources=[]`다.
- source가 없으면 빈 배열을 반환한다.
- read 권한이 없는 workflow는 기존 접근 정책대로 이 endpoint 자체가 차단된다.

예시:

```json
{
  "workflow_id": "uuid",
  "organization_id": "uuid",
  "auth_state": "builder",
  "can_read": true,
  "can_write": true,
  "can_execute": true,
  "can_deploy": false,
  "can_manage": false,
  "sources": [
    {
      "type": "team",
      "team_id": "uuid",
      "team_name": "워크플로우 빌더팀",
      "auth_state": "builder"
    },
    {
      "type": "user",
      "user_id": "uuid",
      "user_name": "혜연",
      "auth_state": "operator"
    }
  ]
}
```

`/apps/operations.permission_sources`는 같은 source schema를 사용한다. `/apps/operations.permission`은 `permissions/me`의 `sources`를 제외한 effective permission summary와 같은 action boolean 의미를 사용하고, `permission_sources`에 source 목록을 둔다.

## MVP 1 변경 기준

- `X-Organization-Id` scope 안 여부는 organization membership helper로 판정한다. Active row는 `organization_auth_state`에 따르고, invited/suspended/removed row는 fail-closed 된다. Membership row 자체가 없는 legacy owner/manager만 호환 fallback으로 organization manager scope를 인정한다.
- `organization_id is null`인 legacy workflow는 active user가 `workflow.created_by`이면 manager fallback을 제한적으로 허용한다. 신규 organization-scoped workflow에는 creator fallback을 적용하지 않는다.
- Backend 기준 `POST /api/v1/apps`, `GET /api/v1/apps`, `POST /api/v1/apps/{app_id}/clone`, `POST /api/v1/workflows`는 `X-Organization-Id` header로 active organization을 명시한다. MBA-71 프론트 변경 기준 공통 `apiClient`와 `attachActiveOrganizationHeader` helper를 통해 org-scoped frontend wrapper가 이 header를 첨부한다.
- 생성자가 만든 workflow에는 user direct `manager` 권한을 부여해 생성 직후 App/Workflow 관리가 가능해야 한다.
- creator 기반 권한 체크를 workflow permission helper로 교체한다.
- workflow execute/stream은 `execute` 권한이 없으면 거부한다.
- draft 저장은 `write` 권한이 없으면 거부한다.
- run list/detail/stats는 `read` 권한이 없으면 거부한다.
- 권한 차단은 `audit_logs`에 `permission.denied`로 기록한다.
- App/Workflow id가 없거나 요청 user의 organization scope 밖이면 `404 resource.not_found`로 숨긴다.
- 같은 organization scope 안에서 resource action 권한만 부족하면 `403 permission.denied`를 반환한다.
