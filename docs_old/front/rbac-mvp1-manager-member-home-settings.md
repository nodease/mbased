# MVP1 Manager/Member Home Settings Split

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: feature/mba-84 working tree

## 목적

이 문서는 `MBA-71` 프론트 작업 전에 manager와 member의 홈/설정 화면 분리 기준을 정리한다.

MVP1의 manager/member 판정은 organization membership 계약을 따른다. 다만 MBA-71의 화면 관리 범위는 전체 organization member 관리가 아니라 team 기반 member 관리로 제한한다. 따라서 이 작업은 organization invitation, accept, member promote/demote UI를 만들지 않고, 현재 `organization`, `organization_memberships`, `teams`, `team_memberships`, resource permission API로 표현 가능한 화면만 다룬다.

## 관련 기준 문서

| 영역 | 문서 |
| --- | --- |
| 문서 권위 | `docs/foundation/document-authority.md` |
| RBAC 정책 | `docs/data-model/rbac-permission-policy.md` |
| active organization | `docs/architecture/auth-rbac.md` |
| organization/team/permission API | `docs/api/organization-rbac.md` |
| app/workflow API | `docs/api/apps-workflows.md` |
| RBAC 화면 개요 | `docs/front/rbac-permission-ui-overview.md` |
| workflow 권한 UI | `docs/front/rbac-workflow-access-matrix.md` |
| RBAC API 연동 | `docs/front/rbac-permission-api-integration.md` |

## 구현 기준

### MVP1에서 사용하는 멤버 기준

MVP1에서 organization 소속과 manager 판정은 active `organization_memberships`를 우선 기준으로 삼는다. Team 소속과 resource 접근은 active `team_memberships`와 resource permission으로 표현한다.

| 개념 | MVP1 구현 기준 |
| --- | --- |
| organization manager | active `organization_memberships.organization_auth_state == "manager"`인 user. Membership row가 없는 legacy data에서만 `organization.created_by` 또는 `organization.managed_by` fallback |
| organization member | active `organization_memberships` row가 있고 manager가 아닌 user |
| team member | `team_memberships` row가 있고 team이 active인 user |
| workspace member | 이 문서에서는 쓰지 않는다. Organization member와 team member 용어만 사용한다. |

### Manager/member 판정 입력

MBA-71 프론트는 `OrganizationResponse.is_manager`를 화면 분기의 기준으로 사용한다.

이 필드는 `docs/api/organization-rbac.md`의 `OrganizationResponse` 계약에 포함되어 있으며, 현재 요청 user 기준으로 계산된다.

| 조건 | `is_manager` |
| --- | --- |
| active `organization_memberships.organization_auth_state == "manager"` | `true` |
| membership row가 없는 legacy data에서 `organization.created_by == current_user.id` | `true` |
| membership row가 없는 legacy data에서 `organization.managed_by == current_user.id` | `true` |
| 그 외 active organization member 또는 active team membership user | `false` |

프론트는 `created_by`, `managed_by`를 직접 계산하지 않는다. Settings와 Home 모두 organization API 응답의 `is_manager`를 신뢰한다.

### MVP1에서 하지 않는 것

- organization invitation API
- invitation accept flow
- organization membership 상태 관리 UI
- `invited`, `active`, `suspended`, `removed` organization member 상태 UI
- member promote/demote
- organization member remove cleanup
- full organization switcher
- team에 속하지 않은 active organization member 관리

## 현재 코드 연결 지점

| 파일 | 현재 역할 | MBA-71에서 확인할 점 |
| --- | --- | --- |
| `apps/client/app/dashboard/page.tsx` | 정적 홈 quick action 화면 | manager/member별 organization/team/workflow summary를 추가할 위치 |
| `apps/client/app/dashboard/settings/page.tsx` | Settings credentials/activity 탭과 member read-only 확인 | organization 운영 기능은 Admin Console로 이동 |
| `apps/client/app/dashboard/admin/page.tsx` | manager-only Admin Console | `OrganizationResponse.is_manager` 기준으로 member/team/권한/credential 관리 UI 진입 제어 |
| `apps/client/app/features/dashboard/components/Sidebar.tsx` | dashboard navigation과 organization 이름 표시 | manager에게만 `관리` 메뉴 노출 |
| `apps/client/lib/activeOrganization.ts` | active organization id localStorage 저장과 `X-Organization-Id` header 생성 | 홈과 설정에서 같은 active organization 기준 사용 |
| `apps/client/app/features/app/api/appApi.ts` | `/apps` API client | 홈에서 접근 가능한 app/workflow 목록 조회 |
| `apps/client/app/features/workflow/api/workflowApi.ts` | workflow 상세와 `/permissions/me` 조회 | workflow별 내 권한 badge 계산 |

## API 연동 기준

### Organization

| API | 용도 | 주의 |
| --- | --- | --- |
| `GET /api/v1/organizations` | 현재 user가 접근 가능한 organization 목록 | active `organization_memberships` 기준. Team membership은 team/resource 접근 표현에 사용한다. |
| `GET /api/v1/organizations/current` | active organization 검증 | `OrganizationResponse.is_manager`로 manager/member 화면을 분기한다. |
| `GET /api/v1/organizations/{organization_id}` | organization 상세 조회 | 필요 시 동일한 `is_manager` 계약을 사용한다. |

`is_manager`는 `api/organization-rbac.md`의 `OrganizationResponse` 계약에 포함된 현재 요청 user 기준 파생 필드다. MBA-71 이후 프론트는 별도 manager 판정 API를 만들지 않고 이 필드를 기준으로 Admin Console, Home summary, manager-only API 호출 여부를 제어한다.

### Manager 전용 Team API

| API | 용도 | 화면 |
| --- | --- | --- |
| `GET /api/v1/users?organization_id={id}` | legacy active organization member user 목록 | Admin Console 전환 후 직접 사용 축소 |
| `GET /api/v1/teams` | team 목록 | manager 홈, Admin Console |
| `GET /api/v1/teams/{team_id}/members` | team별 member map | manager 홈, Admin Console |
| `POST /api/v1/teams` | team 생성 | manager 화면 |
| `PATCH /api/v1/teams/{team_id}` | team 수정 | manager 화면 |
| `DELETE /api/v1/teams/{team_id}` | team soft delete, 즉 비활성화 | manager 화면 |
| `POST /api/v1/teams/{team_id}/members` | team member 추가 | manager 화면 |
| `DELETE /api/v1/teams/{team_id}/members/{user_id}` | team member 제거 | manager 화면 |

`GET /api/v1/teams`는 inactive team도 반환한다. 프론트는 active team과 inactive team을 구분해야 한다. MVP1에서는 inactive team을 숨기거나 `비활성` 배지로 표시한다.

### Member 홈 조회

현재 구현에는 일반 member가 자신의 team 목록만 직접 조회하는 dedicated API가 없다.

가능한 선택지는 다음 중 하나다.

| 선택지 | 설명 | 판단 |
| --- | --- | --- |
| 프론트 우회 없음 | manager 전용 `GET /teams`를 member에게 호출하지 않는다. | 권장 기본값 |
| member용 API 추가 | `GET /api/v1/teams/me` 또는 유사 endpoint로 내 team 목록을 제공한다. | 홈에서 "내 소속 team"이 필수이면 필요 |
| 제한적 화면 | member 홈에는 organization 이름과 accessible app/workflow 권한만 먼저 보여준다. | API 추가 없이 가능한 MVP1 최소 범위 |

MBA-71에서 "member가 자신이 속한 team 목록을 본다"를 반드시 만족하려면 member용 team 조회 API가 필요하다. 현재 `GET /api/v1/teams`와 `GET /api/v1/teams/{team_id}/members`는 manager 전용이므로 member 화면에서 호출하지 않는다.

따라서 MBA-71의 FE-only 기본 구현은 다음으로 제한한다.

- member Home은 organization summary와 접근 가능한 app/workflow 권한 표시를 우선 제공한다.
- member의 team 목록은 dedicated API가 생기기 전까지 섹션을 노출하지 않는다.
- member용 team 조회가 필수 요구가 되면 `GET /api/v1/teams/me` 같은 별도 BE 이슈를 만든다.

### Workflow/App 권한 표시

| API | 용도 | 주의 |
| --- | --- | --- |
| `GET /api/v1/apps` | 접근 가능한 app 목록 | app read는 organization manager 또는 primary workflow read 권한 기준 |
| `GET /api/v1/workflows/{workflow_id}/permissions/me` | 내 workflow effective permission 조회 | `api/apps-workflows.md` 기준 workflow `read` 권한이 필요하므로 `none` 사용자는 보통 선행 403/404로 차단 |

홈에서 app/workflow 권한 badge를 표시하려면 `/apps` 응답의 `workflow_id`가 있는 항목마다 `/workflows/{workflow_id}/permissions/me`를 호출한다. 실패한 항목은 전체 홈 로드를 깨지 말고, 해당 row에 권한 조회 실패 상태를 표시한다.

## 화면 구조

### Manager 홈

Manager 홈은 반복 사용을 위한 운영 화면이어야 한다. 기존 marketing-style quick action만 유지하지 않고, 현재 organization의 team 기반 관리 요약을 추가한다.

권장 섹션:

| 섹션 | 내용 |
| --- | --- |
| Organization summary | organization 이름, 현재 user 권한 `manager` |
| Team management summary | active team 수, inactive team 수, team별 member 수 |
| Team list | team name, active/inactive 상태, member preview, 관리 action |
| Workflow access list | app/workflow 이름, 내 권한, 관리 가능 여부 |

Manager 홈에서 destructive action을 직접 제공할 경우 확인 dialog를 유지한다. 홈에서 모든 form을 넣기보다 Admin Console로 이동하는 action을 둔다.

### Member 홈

Member 홈은 "내가 무엇을 볼 수 있고 무엇을 할 수 있는지"를 먼저 보여준다.

권장 섹션:

| 섹션 | 내용 |
| --- | --- |
| Organization summary | organization 이름, 현재 user 권한 `member` |
| My workflow access | 접근 가능한 app/workflow 목록, `viewer/operator/builder/manager` badge |
| Available actions | 권한별 실행/수정 가능 여부 |

Member에게 manager 전용 CTA를 노출하지 않는다. 권한이 부족한 action은 workflow 실행/수정처럼 이유를 알아야 하는 경우만 disabled와 설명을 사용한다.

### Settings

| 상태 | Settings 탭 |
| --- | --- |
| manager | `LLM Credentials`, `Activity` 표시. 조직 운영은 Admin Console로 이동 |
| member | `LLM Credentials`, `Activity` 표시. 조직 운영 탭 없음 |

Settings는 더 이상 조직 운영 탭을 기본 navigation에 노출하지 않는다. 조직 운영 진입점은 sidebar `관리`와 Home의 관리 CTA로 통일한다.

#### LLM Credentials Settings view

`LLM Credentials` 탭은 manager와 member 모두 볼 수 있지만, Settings에서는 credential 목록 확인과 read-only 안내만 제공한다. Credential 등록, 삭제, model sync, permission grant/revoke는 Admin Console 책임으로 이동한다.

Backend 기준 `POST /api/v1/llm/credentials`는 organization manager 권한을 요구한다. 따라서 Settings에 provider 선택, alias, API key 입력, 등록 버튼을 계속 남기면 member는 값을 입력한 뒤 `403`을 받고, manager는 Admin Console과 Settings 양쪽에서 같은 action을 보게 된다.

API 계약상 credential 삭제와 model sync는 credential `write`, permission grant/revoke는 credential `manage` 또는 organization `manager` 권한으로도 가능하다. 그러나 Admin Console 분리 이후 Settings는 manager/member 모두에게 보수적인 read-only view로 제한한다. 세분화된 credential write/manage UI는 Admin Console 후속 작업에서 다룬다.

| 기능 | organization manager | member |
| --- | --- | --- |
| credential 목록 조회 | 가능 | 접근 권한이 있는 credential만 가능 |
| credential 등록 form | Settings에서는 숨김 | 숨김 |
| credential 등록 버튼 | Settings에서는 숨김 | 숨김 |
| credential 삭제 | Settings에서는 숨김 | 숨김 |
| credential model sync | Settings에서는 숨김 | 숨김 |
| credential permission grant/revoke | Settings에서는 숨김 | 숨김 |

credential이 없는 경우에도 Settings에서는 등록 CTA를 보여주지 않는다. 대신 "등록, 삭제, 모델 동기화는 관리 화면에서 다룹니다" 성격의 read-only 안내를 보여준다.

## 권한별 UI 동작

| 화면/행동 | manager | member |
| --- | --- | --- |
| Admin Console 관리 메뉴 | 표시 | 숨김 또는 접근 차단 |
| team 목록 조회 | 가능 | dedicated API 없으면 미노출 |
| team 생성 | 가능 | 숨김 |
| team 수정 | 가능 | 숨김 |
| team 비활성화 | 가능 | 숨김 |
| team member 추가/제거 | 가능 | 숨김 |
| workflow permission grant/revoke | 가능 | 숨김 |
| LLM credential permission grant/revoke | Admin Console에서 가능 | 숨김 |
| LLM credential 등록 | Admin Console에서 가능 | 숨김 |
| LLM credential 삭제/동기화 | Admin Console에서 가능 | 숨김 |
| app/workflow 목록 | 접근 권한 기준 표시 | 접근 권한 기준 표시 |
| workflow 권한 badge | 표시 | 표시 |

## Error/empty/loading

| 상태 | 표시 기준 |
| --- | --- |
| loading | organization, teams, apps, permissions를 나눠 skeleton 또는 inline loading 표시 |
| empty organization | 접근 가능한 organization 없음 |
| empty team | manager에게 team 생성 CTA 제공. member에게는 "소속 team 없음" 표시 |
| inactive team | active team처럼 조작 가능한 row로 보이지 않게 숨김 또는 badge 표시 |
| permission denied | member가 manager 전용 화면/action에 접근한 경우 |
| API error | HTTP status와 action 기준 메시지. `Request failed`만 노출하지 않는다. |

## 구현 순서

MBA-71 이후 조직 운영 기능은 Settings에서 Admin Console로 이동한다. Settings는 개인/credential read-only/activity 중심으로 축소하고 Home의 조직 접근 CTA도 Admin Console로 보낸다.

| 순서 | 작업 | 기준 |
| --- | --- | --- |
| 1 | `OrganizationResponse` 프론트 타입 확인 | `is_manager`를 필수 필드로 사용 |
| 2 | Settings tab 정리 | manager/member 모두 조직 운영 탭 미노출, 기본 탭은 `LLM Credentials` |
| 3 | Settings API 호출 분리 | manager 전용 API는 `OrganizationResponse.is_manager === true`일 때만 호출 |
| 4 | Settings error state 정리 | member 화면에서 manager-only API 실패가 `Request failed`로 노출되지 않게 처리 |
| 5 | LLM Credentials action guard | member에게 credential 등록/삭제/동기화/권한 관리 UI 미노출 |
| 6 | Manager Home 보강 | team summary, inactive team 구분, workflow access summary |
| 7 | Member Home 보강 | organization summary, 접근 가능한 app/workflow, workflow permission badge |
| 8 | QA | manager/member 계정으로 Settings와 Home을 각각 확인 |

## 구현 메모

1. `OrganizationResponse.is_manager`는 API 응답 계약으로 존재하므로 프론트는 이 값을 기준으로 분기한다.
2. `SettingsPage`는 manager/member 모두에게 조직 운영 탭을 렌더링하지 않는다.
3. `SettingsPage.loadData()`는 organization member/team/permission 관리 API를 호출하지 않는다. 조직 운영 데이터는 Admin Console에서 로드한다.
4. `SettingsPage`는 manager/member 모두에게 LLM credential 등록 form, 삭제, sync, permission grant/revoke action을 렌더링하지 않는다.
5. Home은 organization/app/workflow permission 조회를 병렬화하되, 개별 permission 조회 실패가 전체 Home 실패가 되지 않게 한다.
6. inactive team은 `is_active`와 `deactivated_at`을 기준으로 UI에서 구분한다.
7. member용 "내 team 목록"은 현재 API가 없으므로 MBA-71 FE-only 범위에서는 manager 전용 API를 우회 호출하지 않는다.
8. Settings 내부의 legacy 조직 접근 branch/handler는 현재 navigation에서 접근되지 않는다. Admin Console action 연결이 완료되면 제거 범위를 별도로 정리한다.

## QA 체크리스트

- [ ] manager 로그인 시 sidebar `관리`와 `/dashboard/admin`이 보인다.
- [ ] Admin Console 멤버 탭에서 member 목록과 상태 badge가 보이고 초대/정지/재활성화/승격/강등/제거가 동작한다.
- [ ] Admin Console 팀 탭에서 team 목록과 기존 member가 보이고 생성/수정/비활성화/member 추가/member 제거가 동작한다.
- [ ] Admin Console 권한 탭에서 workflow와 LLM credential의 team/user direct 권한 조회/저장/회수가 동작한다.
- [ ] Admin Console LLM Credentials 탭은 목록을 표시하고 등록/삭제/sync/권한 관리 진입이 동작한다.
- [ ] inactive team은 active team과 구분된다.
- [ ] member 로그인 시 sidebar `관리`와 Settings 조직 운영 탭이 보이지 않는다.
- [ ] member 로그인 시 LLM credential 등록 form과 등록 버튼이 보이지 않는다.
- [ ] member 로그인 시 LLM credential 삭제와 model sync action이 보이지 않는다.
- [ ] manager 로그인 시에도 Settings에서는 LLM credential 등록/삭제/sync action이 보이지 않는다.
- [ ] member가 설정에 직접 진입해도 manager 전용 API 실패가 전체 화면을 깨지 않는다.
- [ ] member 홈에서 접근 가능한 app/workflow 목록이 보인다.
- [ ] member 홈에서 workflow 권한 badge가 표시된다.
- [ ] member는 manager 전용 action을 사용할 수 없다.
- [ ] API 403/404가 사용자 행동 기준 메시지로 표시된다.
- [ ] 기존 LLM Credentials 탭과 Activity 탭이 깨지지 않는다.

## 남은 결정

| 항목 | 필요한 결정 |
| --- | --- |
| member용 내 team 목록 | `GET /teams/me` 같은 API를 후속 BE 이슈로 추가할지 |
| inactive team UX | 숨김과 비활성 배지 중 어느 방식을 기본으로 할지 |
| 홈 화면 배치 | 기존 quick action을 유지할지, manager/member 요약을 첫 화면 상단으로 올릴지 |
