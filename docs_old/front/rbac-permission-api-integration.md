# RBAC Permission API Integration

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: feature/mba-84 working tree

## 목적

이 문서는 RBAC 프론트 구현에서 사용할 organization, team, workflow permission API 연결 방식과 프론트 상태 반영 기준을 정리한다.

API 계약의 최종 기준은 `api/organization-rbac.md`와 `api/apps-workflows.md`다. 이 문서에 코드 연결 편의를 위해 적은 endpoint가 API 문서에 없으면 확정 계약이 아니라 문서 보강 TODO로 취급한다.

## 범위

포함:

- active organization header 처리
- organization/member/team 로딩
- active organization member picker 기준
- workflow permission 목록 조회
- workflow team/user permission grant/revoke
- 내 workflow permission 조회
- workflow 권한 기반 UI state 반영
- API error 처리

제외:

- API route 신규 설계
- 서버 permission helper 구현
- LLM credential permission 세부 UI. 단, 기존 Settings의 LLM credential permission user picker source는 MBA-84 공통 picker 전환 범위에 포함한다.

## 관련 기준 문서

| 문서 | 기준 |
| --- | --- |
| `api/organization-rbac.md` | organization/team/permission API |
| `api/apps-workflows.md` | workflow API |
| `architecture/auth-rbac.md` | `X-Organization-Id` active organization |
| `data-model/rbac-permission-policy.md` | permission matrix |

## 공통 요청 규칙

인증된 API는 cookie 기반 `credentials: include` 또는 axios `withCredentials: true`를 사용한다.

organization/team/permission 관리 API는 `X-Organization-Id` header가 필요하다.

```ts
headers: {
  'Content-Type': 'application/json',
  'X-Organization-Id': activeOrganizationId,
}
```

active organization은 서버 session에 저장하지 않는다. 프론트가 현재 선택한 organization id를 매 요청에 넣어야 한다.

## API 목록

### Organization

| API | 사용 시점 | 성공 시 state |
| --- | --- | --- |
| `GET /api/v1/organizations` | Settings 또는 app 초기화 | 접근 가능한 organization 목록 |
| `GET /api/v1/organizations/memberships` | organization switcher 또는 초대 수락 화면 | active/invited organization membership 목록 |
| `GET /api/v1/organizations/current` | `X-Organization-Id` 준비 후 active organization 검증 | 현재 organization |
| `GET /api/v1/organizations/{organization_id}` | 상세 필요 시 | organization detail |

### Organization Member

MBA-84 기준 organization member API/type/picker는 MBA-82 manager 화면과 MBA-83 member 화면의 선행 공통 기반이다. 화면 전체 구현은 MBA-82/MBA-83에서 진행하고, 이 문서는 공통 타입, API client, picker가 따라야 할 계약만 정의한다.

| API | 사용 시점 | 성공 시 state |
| --- | --- | --- |
| `GET /api/v1/organizations/{organization_id}/members` | manager가 organization member 목록 또는 active member picker source를 로드 | `OrganizationMember[]` |
| `POST /api/v1/organizations/{organization_id}/members/invitations` | manager가 기존 가입 user를 organization에 초대 | `OrganizationMember` |
| `POST /api/v1/organizations/{organization_id}/members/me/accept` | 초대받은 user가 본인 초대를 수락 | `OrganizationMember` |
| `PATCH /api/v1/organizations/{organization_id}/members/{user_id}` | manager가 member state/auth state 변경 | `OrganizationMember` |
| `DELETE /api/v1/organizations/{organization_id}/members/{user_id}` | manager가 member 제거와 연결 권한 cleanup 수행 | `OrganizationMemberRemoveResponse` |

`GET /members`는 `state=active|invited|suspended|removed` query를 받을 수 있다. Query가 없으면 API는 active, invited, suspended member를 반환하고 removed member는 명시 query가 있을 때만 반환한다. Active member picker는 이 기본 응답을 그대로 쓰지 말고 client에서 `membership_state === 'active'`를 다시 필터링해야 한다.

Organization member 관리 API 중 list/invite/update/remove는 path의 `organization_id`와 `X-Organization-Id`가 일치해야 한다. Manager 화면에서 호출하는 list/invite/update/remove API는 organization `manager` 권한이 필요하다. `acceptInvitation`은 `X-Organization-Id`를 요구하거나 검증하지 않고 path `organization_id`와 현재 user의 invited membership으로 처리하는 본인 API이며 manager-only API가 아니다.

### Team / Member

| API | 사용 시점 | 성공 시 state |
| --- | --- | --- |
| `GET /api/v1/teams` | Admin Console 또는 현재 navigation에서 접근되지 않는 legacy Settings access branch | team 목록 |
| `GET /api/v1/teams/{team_id}/members` | team 목록 로드 후 | team별 member map |
| `POST /api/v1/teams` | team 생성 | team 목록 재조회 |
| `POST /api/v1/teams/{team_id}/members` | member 추가 | team member 재조회 |
| `DELETE /api/v1/teams/{team_id}/members/{user_id}` | member 제거 | team member 재조회 |

Team member 추가 대상은 active organization member여야 한다. Team membership API는 organization membership을 새로 만들지 않으므로, 프론트는 team member picker source를 organization member API 또는 그 기반의 `ActiveOrganizationMemberPicker`로 통일한다.

### Workflow permission 관리

| API | 사용 시점 | 성공 시 state |
| --- | --- | --- |
| `GET /api/v1/permissions/workflows/{workflow_id}` | 권한 관리 화면에서 workflow 선택 | team/user permission 목록 |
| `PUT /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}` | team 권한 저장 | permission 목록 재조회 |
| `DELETE /api/v1/permissions/workflows/{workflow_id}/teams/{team_id}` | team 권한 회수 | permission 목록 재조회 |
| `PUT /api/v1/permissions/workflows/{workflow_id}/users/{user_id}` | user direct 권한 저장 | permission 목록 재조회 |
| `DELETE /api/v1/permissions/workflows/{workflow_id}/users/{user_id}` | user direct 권한 회수 | permission 목록 재조회 |

### Workflow 사용 권한

| API | 사용 시점 | 성공 시 state |
| --- | --- | --- |
| `GET /api/v1/workflows/{workflow_id}` | workflow 진입 | metadata |
| `GET /api/v1/workflows/{workflow_id}/draft` | editor graph 로드 | graph |
| `POST /api/v1/workflows/{workflow_id}/draft` | 저장 | save status |
| `POST /api/v1/workflows/{workflow_id}/execute` | 실행 | run result |
| `POST /api/v1/workflows/{workflow_id}/stream` | streaming 실행 | event stream |

`GET /api/v1/workflows/{workflow_id}/permissions/me`는 `api/apps-workflows.md`의 공식 계약에 포함된 endpoint다. 호출에는 workflow `read` 권한이 필요하므로, 프론트는 이 응답을 "이미 읽을 수 있는 workflow의 내 effective permission" 조회로 사용한다. MBA-74 이후 권한 출처(team/direct)는 같은 응답의 `sources`로 표시한다. 전체 권한 관리 표가 필요할 때만 `api/organization-rbac.md`의 permission 목록 API를 별도로 조회한다.

## Response 매핑

### Organization member

```ts
type MembershipState = 'invited' | 'active' | 'suspended' | 'removed';

type OrganizationAuthState = 'member' | 'manager';

type OrganizationSummary = {
  id: string;
  name: string;
  membership_state: MembershipState;
  organization_auth_state: OrganizationAuthState;
  is_active: boolean;
};

type OrganizationMember = {
  id: string;
  organization_id: string;
  user_id: string;
  user_email: string;
  user_name: string;
  membership_state: MembershipState;
  organization_auth_state: OrganizationAuthState;
  invited_by: string | null;
  invited_at: string | null;
  accepted_at: string | null;
  removed_at: string | null;
  created_at: string;
  updated_at: string;
};

type OrganizationMemberInviteRequest = {
  user_id: string;
  organization_auth_state?: OrganizationAuthState;
};

type OrganizationMemberUpdateRequest = {
  membership_state?: Extract<MembershipState, 'active' | 'suspended'> | null;
  organization_auth_state?: OrganizationAuthState | null;
};

type OrganizationMemberRemoveResponse = {
  status: string;
  removed_team_memberships: number;
  revoked_user_permissions: {
    workflow: number;
    llm_credential: number;
    knowledge_base: number;
    audit: number;
  };
};
```

UI 매핑:

| Response field | UI |
| --- | --- |
| `membership_state` | `MemberStateBadge` |
| `organization_auth_state` | `OrganizationAuthBadge` |
| `user_name`, `user_email` | member row label, picker option label |
| `invited_at`, `accepted_at`, `removed_at` | member detail 또는 상태 설명 |
| `removed_team_memberships`, `revoked_user_permissions` | remove cleanup 안내 |

`MemberStateBadge`는 invited/active/suspended/removed를 구분한다. `OrganizationAuthBadge`는 member/manager를 구분한다. Badge는 상태 이름만 보여주고 권한 변경 action은 MBA-82 manager 화면에서 별도 control로 다룬다.

### Active organization member picker

`ActiveOrganizationMemberPicker`는 organization manager 화면에서 team member 추가와 user direct permission 부여 UI가 공통으로 사용할 picker다.

필수 정책:

- source는 `OrganizationMember[]`이며 기본 API는 manager-only `GET /api/v1/organizations/{organization_id}/members`다.
- picker option은 `membership_state === 'active'` member만 포함한다.
- invited/suspended/removed member는 선택 불가 상태로 보여주기보다 기본적으로 제외한다.
- option value는 `user_id`다.
- option label은 `user_name`을 우선하고 보조 text로 `user_email`을 표시한다.
- `excludedUserIds`를 지원해 이미 team에 속한 user처럼 화면에서 중복 선택을 막아야 하는 대상을 제외할 수 있다.
- user direct permission picker는 기존 직접 권한 보유자를 제외하지 않는다. Grant API가 `PUT` upsert이므로 같은 user를 다시 선택해 `auth_state`를 수정할 수 있어야 한다.
- manager 화면의 direct permission picker는 team membership 여부와 무관하게 active organization member를 표시할 수 있어야 한다.
- 긴 name/email은 picker width가 parent를 밀어내지 않도록 제한한다. Native select 구현에서는 `name (email)` 한 줄 label을 사용하고, 별도 custom dropdown으로 확장할 때 2줄 layout을 적용할 수 있다.

Settings 전환 기준:

- team member picker는 기존 `GET /api/v1/users?organization_id={id}` 직접 사용에서 active organization member source로 전환한다.
- manager 화면의 direct permission user picker도 같은 active organization member source를 사용한다.
- `GET /api/v1/users`는 manager 전용 active member user 목록으로 남아 있지만, MBA-84 이후 picker의 기준 타입은 `OrganizationMember`다.
- organization manager가 아닌 workflow manager는 permission grant 권한이 있더라도 manager-only member list API를 읽지 못할 수 있다. 이 경우 필요한 member search/list 계약은 MBA-84 범위가 아니라 후속 BE/API 이슈로 분리한다.
- MBA-82/MBA-83 전까지 manager/member 화면 전체를 재구성하지 않고, 기존 화면이 이 picker로 교체될 수 있는 API/type boundary를 먼저 만든다.

### Permission list

```ts
type ResourcePermissionListResponse = {
  resource_type: 'workflow';
  resource_id: string;
  organization_id: string;
  team_permissions: ResourcePermissionEntry[];
  user_permissions: ResourcePermissionEntry[];
};
```

UI 매핑:

| Response field | UI |
| --- | --- |
| `team_permissions` | Team permissions table |
| `user_permissions` | User direct permissions table |
| `auth_state` | 권한 badge/select |
| `assigned_at` | 부여 시각 |

### My workflow permission

```ts
type WorkflowPermissionResponse = {
  workflow_id: string;
  organization_id: string | null;
  auth_state: 'none' | 'viewer' | 'operator' | 'builder' | 'manager';
  can_read: boolean;
  can_write: boolean;
  can_execute: boolean;
  can_deploy: boolean;
  can_manage: boolean;
  sources: WorkflowPermissionSource[];
};

type WorkflowPermissionSource =
  | {
      type: 'team';
      team_id: string;
      team_name: string;
      auth_state: 'viewer' | 'operator' | 'builder' | 'manager';
    }
  | {
      type: 'user';
      user_id: string;
      user_name?: string | null;
      auth_state: 'viewer' | 'operator' | 'builder' | 'manager';
    };
};
```

UI는 가능하면 `can_*` boolean을 직접 사용하고, 표시 badge에는 `auth_state`를 사용한다. 접근 경로 표시에는 `sources`를 사용한다. `sources=[]`는 오류가 아니라 source가 없는 override 또는 legacy fallback일 수 있다.

## Error 처리

| Status | 의미 | UI 처리 |
| --- | --- | --- |
| 400 | 잘못된 form 또는 auth_state | field error |
| 401 | 인증 만료 | login redirect 또는 session expired 안내 |
| 403 | 같은 organization scope 안이지만 action 권한 부족 | permission denied 안내 |
| 404 | resource 없음 또는 organization scope 밖 | not found 또는 접근 차단 안내 |
| 409 | 중복/동시성 충돌 가능성 | 재조회 후 다시 시도 |
| 500 | 서버 오류 | retry 안내 |
| Network | 서버 연결 실패 | 네트워크/서버 상태 확인 안내 |

## 상태 관리 기준

권장 상태:

```ts
type RbacState = {
  activeOrganizationId: string | null;
  organizations: OrganizationResponse[];
  organizationMembersByOrganizationId: Record<string, OrganizationMember[]>;
  teams: TeamResponse[];
  teamMembersByTeamId: Record<string, TeamMemberResponse[]>;
  selectedWorkflowId: string | null;
  workflowPermissionsById: Record<string, ResourcePermissionListResponse>;
  workflowAccessById: Record<string, WorkflowPermissionResponse>;
  loading: boolean;
  submitting: boolean;
  error: string | null;
};
```

권한 변경 후에는 optimistic update보다 재조회를 기본으로 한다. permission row upsert, audit, effective permission 계산이 서버 기준이기 때문이다.

## 현재 코드 연결 지점

| 파일 | 역할 |
| --- | --- |
| `apps/client/app/dashboard/settings/page.tsx` | credential/activity 중심 Settings UI. organization/team/permission 관리는 Settings navigation에서 제거됐고 legacy branch/handler 정리는 후속 |
| `apps/client/lib/activeOrganization.ts` | active organization id 저장과 header 생성 |
| `apps/client/app/features/organization/api/organizationApi.ts` | MBA-84 organization/member API client |
| `apps/client/app/features/organization/components/ActiveOrganizationMemberPicker.tsx` | MBA-84 active organization member picker |
| `apps/client/app/features/organization/components/MemberStateBadge.tsx` | organization member 상태 badge |
| `apps/client/app/features/organization/components/OrganizationAuthBadge.tsx` | organization member/manager 권한 badge |
| `apps/client/app/features/organization/utils/memberFilters.ts` | active member와 제외 user id 필터링 |
| `apps/client/app/features/workflow/api/workflowApi.ts` | workflow API client |
| `apps/client/app/features/workflow/hooks/useWorkflowAppSync.ts` | workflow metadata와 내 permission 로드 |
| `apps/client/app/features/workflow/store/useWorkflowStore.ts` | workflow access state 저장 |

## 확인 필요

| 항목 | 이유 |
| --- | --- |
| 기존 Settings picker의 `GET /api/v1/users?organization_id={id}` 직접 사용 제거 범위 | MBA-84에서 공통 picker로 교체 가능한 부분까지 진행하고, 화면 구조 개편은 MBA-82에서 처리 |
| `GET /api/v1/workflows/app/{app_id}`의 workflow별 permission filtering | `none` workflow 목록 노출 정책과 직접 연결 |
| `permissions/me`가 `none` 상태에서도 응답할지 | 현재 공식 계약은 workflow `read` 권한을 요구하므로 일반적인 `none` 사용자는 403/404로 차단된다. `none` 응답은 관리자 진단용 별도 API가 생기기 전까지 UI 전제로 두지 않음 |

## QA 체크리스트

- [ ] 모든 permission 관리 API에 `X-Organization-Id`가 포함된다.
- [ ] organization 변경 시 Admin Console member/team state가 재조회된다.
- [ ] active member picker는 action 연결 후 active organization member만 표시한다.
- [ ] team member 추가 picker 연결 후 invited/suspended/removed member는 제외된다.
- [ ] direct permission picker 연결 후 team에 없는 active member도 표시할 수 있다.
- [ ] workflow permission UI 연결 후 workflow 변경 시 permission list가 재조회된다.
- [ ] grant/update action 연결 후 서버 목록과 UI가 일치한다.
- [ ] revoke action 연결 후 row가 사라진다.
- [ ] 403 응답 시 권한 부족 메시지를 보여준다.
- [ ] 404 응답 시 존재 여부를 과도하게 노출하지 않는다.
