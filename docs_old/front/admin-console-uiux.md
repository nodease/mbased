# Admin Console UI/UX

Status: Draft
Authority: Frontend Implementation Guide
Source of Truth: No
Verified Against: feature/mba-84 working tree

## 목적

이 문서는 organization manager 전용 관리 화면을 `/dashboard/settings`에서 분리해 `/dashboard/admin` 또는 동등한 manager-only 관리 콘솔로 구성할 때의 UI/UX 기준을 정리한다.

기존 Settings는 개인/계정/읽기 전용 확인 성격으로 남기고, 조직 멤버, 팀, 권한, LLM Credential, 지식 기반, 감사 로그처럼 조직 운영자가 반복적으로 조작하는 기능은 Admin Console로 이동한다.

## 범위

포함:

- dashboard sidebar에서 manager에게만 보이는 `관리` 1차 메뉴
- Admin Console 내부 상단 탭 정보 구조
- Settings와 Admin Console의 책임 분리
- manager-only 화면의 기본 layout, empty/loading/error 상태
- MBA-82/MBA-83/MBA-69에서 이어질 화면 확장 기준

제외:

- 신규 API 계약 설계
- backend permission enforcement 변경
- organization invitation/accept 상세 화면 구현
- 지식 기반 권한 API가 없는 영역의 세부 구현
- 모든 관리 action을 한 번에 구현하는 것

## 관련 기준 문서

| 영역 | 문서 |
| --- | --- |
| RBAC 화면 개요 | `docs/front/rbac-permission-ui-overview.md` |
| manager/member 홈·설정 분리 | `docs/front/rbac-mvp1-manager-member-home-settings.md` |
| organization member API/type/picker | `docs/front/rbac-permission-api-integration.md` |
| workflow 권한 관리 흐름 | `docs/front/rbac-permission-management-flow.md` |
| 내 모듈 권한/운영 목록 | `docs/front/module-list-access-operations-ui.md` |
| RBAC 정책 | `docs/data-model/rbac-permission-policy.md` |
| active organization | `docs/architecture/auth-rbac.md` |
| organization/team/permission API | `docs/api/organization-rbac.md` |

## 정보 구조

### Sidebar

Dashboard sidebar에는 manager에게만 `관리` 메뉴를 노출한다. Member에게는 메뉴 자체를 숨긴다.

```text
홈
내 모듈
지식 기반
통계
관리        # organization manager only
설정
```

`관리`는 Settings 하위 탭이 아니라 sidebar 1차 메뉴다. 조직 운영 기능은 사용 빈도와 책임이 Settings보다 크므로, Settings 안에 계속 누적하지 않는다.

### Admin Console Tabs

Admin Console 내부는 상단 탭으로 나눈다.

```text
관리
<Organization Name> · 관리자

[멤버] [팀] [권한] [LLM Credentials] [지식 기반] [감사 로그] [조직 설정]
```

MVP 우선순위:

| 순서 | 탭 | MVP 판단 |
| --- | --- | --- |
| 1 | 멤버 | MBA-82 manager 화면의 첫 진입점 |
| 2 | 팀 | team 기반 member 관리와 권한 부여의 기반 |
| 3 | 권한 | workflow/team/user direct permission 관리 |
| 4 | LLM Credentials | credential 등록, 삭제, 동기화, 접근 권한 |
| 5 | 지식 기반 | knowledge base 접근/관리 권한. API 준비 상태에 따라 read-only 또는 placeholder 가능 |
| 6 | 감사 로그 | 권한/멤버/credential 변경 이력 |
| 7 | 조직 설정 | 조직명, manager 목록, 기본 정책. MVP에서는 후순위 가능 |

첫 화면은 `멤버` 탭을 기본으로 한다. Manager가 가장 먼저 확인해야 하는 것은 “누가 이 조직에 있고 어떤 상태인가”이기 때문이다.

## Settings와 Admin Console 책임 분리

| 영역 | Settings | Admin Console |
| --- | --- | --- |
| 계정/개인 설정 | 표시 | 표시하지 않음 |
| 현재 organization membership 요약 | read-only 표시 가능 | 운영 header에 표시 |
| 조직 접근 관리 | 이동 링크만 표시하거나 제거 | 주 기능 |
| Team 생성/비활성화/member 추가 | 표시하지 않음 | 주 기능 |
| Workflow 권한 grant/revoke | 표시하지 않음 | 주 기능 |
| LLM Credential 목록 | member read-only 가능 | manager 관리 기능 |
| LLM Credential 등록/삭제/sync | 표시하지 않음 | 주 기능 |
| 지식 기반 접근 권한 | 표시하지 않음 | 주 기능 또는 후속 탭 |
| Activity/Audit | 내 활동 중심 | 조직 감사 로그 중심 |

Settings는 “내가 보는 설정”이고 Admin Console은 “조직을 운영하는 화면”이다. 이 구분을 화면 제목, navigation, empty state 문구에서도 유지한다.

## 탭별 화면 기준

### 공통 목록 UX

Admin Console의 `멤버`, `팀`, `권한`, `LLM Credentials` 탭은 row 수가 늘어나는 운영 화면이다. 단순히 전체 row를 세로로 나열하면 manager가 반복 업무에서 대상을 찾기 어렵고, 실수로 다른 대상에 destructive action을 수행할 위험이 커진다.

공통 원칙:

- 목록 상단에는 검색, 주요 필터, 결과 수, 새로고침 또는 reload 상태를 둔다.
- 검색은 이름/email/설명처럼 사람이 기억하는 label 중심으로 동작한다.
- 필터는 status, auth state, active state, resource type처럼 운영 판단에 직접 쓰이는 기준만 우선 제공한다.
- row action은 오른쪽에 모으고, destructive action은 바로 실행하지 않고 확인 dialog를 거친다.
- pagination 또는 `더 보기`는 row가 많아질 수 있는 탭에 기본으로 둔다. 서버 pagination API가 없으면 client-side pagination을 임시로 적용하되, 문서와 코드에 임시 범위를 명시한다.
- 선택한 row의 세부 조작은 table 안에 form을 펼치기보다 drawer 또는 side panel을 우선한다. Table은 스캔과 선택에 집중시키고, 변경 작업은 별도 surface에서 처리한다.
- empty state는 "없음"만 말하지 않고 다음 가능한 action을 보여준다. 단, API나 정책이 없는 action은 disabled CTA와 후속 범위를 함께 표시한다.

탭별 최소 목록 control:

| 탭 | 검색 | 필터 | pagination |
| --- | --- | --- | --- |
| 멤버 | 이름, email | membership state, organization auth state | 필요 |
| 팀 | 팀 이름, 설명 | active/inactive, member count 유무 | 필요 |
| 권한 | resource 이름, grantee 이름 | resource type, grantee type, auth state | 필요 |
| LLM Credentials | provider, credential name | provider, valid/invalid | 필요 |
| 지식 기반 | 이름, 설명 | 연결/문서 상태. 권한 API 확정 후 auth state | 필요 |
| 감사 로그 | actor/action/target | 기간, action type, status | 서버 pagination 필요 |

MVP에서는 `멤버`와 `팀` 탭부터 검색/필터/client pagination을 붙인다. 권한/credential 권한 관리는 실제 mutation UI 연결 시 같은 목록 패턴을 적용한다.

MVP pagination 규칙:

- 기본 page size는 20개다.
- 검색어 또는 필터가 바뀌면 page는 1로 reset한다.
- 서버 pagination API가 없는 목록은 client-side pagination을 임시로 적용한다.
- 서버 pagination API가 생긴 목록은 query 기반 pagination으로 전환하되, toolbar와 table UX는 유지한다.
- URL query 동기화는 MVP 기본 범위에 넣지 않는다. 운영 목록 공유/뒤로가기 요구가 명확해지면 후속으로 추가한다.

### Destructive Action 기준

운영 콘솔의 위험 action은 모두 같은 confirm을 쓰지 않는다. 실수 비용과 반복 사용 피로도를 기준으로 등급을 나눈다.

| 등급 | 예시 | UI 기준 |
| --- | --- | --- |
| 일반 confirm | team member 제거, 권한 회수 | 짧은 confirm dialog |
| 영향 요약 confirm | organization member 제거, team 비활성화, LLM credential 사용 중지 | 영향을 받는 team/permission/workflow/model 정보를 요약한 confirm |
| action 차단 | 마지막 manager 강등/제거, 자기 자신 제거/강등 | action disabled와 이유 tooltip 또는 inline message |

서버가 guard error를 반환하더라도 프론트는 가능한 한 action 전 단계에서 위험을 설명한다. 서버 error는 최종 방어선으로 보고, 사용자에게는 행동 기준 메시지로 번역한다.

### 멤버

목적: organization member 상태와 조직 권한을 관리한다.

주요 UI:

- 검색 input
- 상태 필터: 전체, 초대 중, 활성, 정지, 제거됨
- 조직 권한 필터: 전체, 멤버, 관리자
- 초대 button
- table: 이름, 이메일, 상태, 조직 권한, 소속 팀, 최근 변경, 작업

작업:

- 초대
- 정지/재활성화
- manager 승격/member 강등
- 제거
- 마지막 manager 보호 error 표시
- 자기 자신 강등/제거 guard 표시

UI/UX 구현 기준:

- 상단 toolbar는 검색 input, 상태 필터, 조직 권한 필터, `초대` CTA로 구성한다.
- 초대는 drawer 또는 modal에서 수행한다. 현재 구현 기준 입력은 기존 가입 user의 UUID와 조직 권한 선택으로 제한하고, 초대 후 row를 재조회한다.
- 초대 drawer에는 "가입된 사용자 UUID만 초대할 수 있습니다"를 명시한다. Email 검색/user picker 초대는 user directory API가 생긴 뒤 후속으로 연결한다.
- row action은 `정지`, `재활성화`, `관리자 승격`, `멤버로 강등`, `제거`를 상태와 권한에 따라 노출한다.
- `제거`는 cleanup 결과를 보여줘야 한다. `OrganizationMemberRemoveResponse.removed_team_memberships`, `revoked_user_permissions`를 confirm 또는 success message에 반영한다.
- 마지막 manager 제거/강등, 자기 자신 제거/강등은 서버 error를 그대로 노출하지 않고 "마지막 관리자는 제거할 수 없습니다"처럼 행동 기준 메시지로 표시한다.
- invited member는 아직 accept 전이므로 `정지`보다 `제거` 또는 후속 `초대 취소/재전송` 정책을 우선 검토한다. 초대 취소/재전송 API가 확정되기 전까지는 별도 action으로 약속하지 않는다.

초대 재전송/취소는 현재 확정 API 계약에 없으므로 기본 action으로 약속하지 않는다. 해당 UX가 필요하면 resend/cancel API 또는 기존 update/remove API로 대체 가능한지 별도 확인 후 후속 이슈로 분리한다.

MVP에서 invite/accept UI flow가 아직 연결되지 않은 경우 초대 action은 disabled 또는 후속 이슈 안내로 둔다. 상태 badge는 MBA-84 구현을 재사용한다. `ActiveOrganizationMemberPicker`는 이미 organization에 속한 active member를 고르는 용도이므로 초대 대상 선택에는 사용하지 않고, team member 추가와 user direct permission 부여에만 사용한다.

### 팀

목적: active organization member를 team에 배정하고 team lifecycle을 관리한다.

주요 UI:

- 팀 생성 button
- active/inactive segmented control
- team table과 row detail drawer
- team detail drawer: member list, member 추가, member 제거

작업:

- team 생성
- team 이름/설명 수정
- team 비활성화
- active organization member만 team에 추가
- 이미 team에 속한 member 중복 제외
- suspended/removed member가 기존 team에 남아 있는 경우 cleanup 안내

UI/UX 구현 기준:

- 상단 toolbar는 검색 input, active/inactive 필터, `팀 생성` CTA로 구성한다.
- 팀 생성/수정은 drawer 또는 compact modal을 사용한다. 필드는 `name`, `description`, 필요 시 `is_auto_add`로 제한한다.
- 팀 row에는 member preview를 모두 늘어놓지 않는다. 많은 member가 있는 팀은 첫 몇 명만 보여주고 `+N` 또는 detail drawer 진입을 제공한다.
- 팀 detail drawer에서 member 추가/제거를 처리한다. 추가 대상은 `ActiveOrganizationMemberPicker`를 사용하고 이미 해당 team에 속한 user는 제외한다.
- 팀 비활성화는 delete가 아니라 soft deactivate 성격이므로 label은 `비활성화`로 표시한다. 비활성화 confirm에는 기존 team permission과 member assignment가 화면에서 어떻게 보일지 안내한다.
- inactive team에는 member 추가와 permission grant action을 막고, 필요한 경우 read-only detail만 보여준다.

### 권한

목적: workflow resource에 대해 team/user direct permission을 관리한다.

주요 UI:

- resource selector: workflow 우선
- grantee type: team, user direct
- grantee picker: team list 또는 active organization member picker
- auth state selector: viewer, operator, builder, manager
- permission table: team permissions, user direct permissions

작업:

- team permission grant/update/revoke
- user direct permission grant/update/revoke
- 권한 출처 확인은 일반 member 화면에서는 `/apps/operations.permission_sources` 또는 `/permissions/me.sources`로 read-only 표시하고, Admin Console에서는 전체 권한 관리 표로 다룬다.

UI/UX 구현 기준:

- 권한 탭은 먼저 resource selector를 요구한다. MVP에서는 workflow를 우선 연결하고, LLM credential 권한은 `LLM Credentials` 탭 row에서 진입하는 방식도 허용한다.
- 권한 목록은 `team permissions`와 `user direct permissions`를 분리해서 보여주되, 검색과 auth state 필터는 공통 toolbar에서 적용한다.
- 권한 부여/수정은 inline row form보다 side panel을 우선한다. `resource`, `grantee type`, `grantee`, `auth state`를 한 번에 선택하고 저장한다.
- 같은 grantee에 이미 권한이 있으면 `부여`가 아니라 `수정`으로 표시한다. API는 `PUT` upsert지만 UI 문구는 사용자의 의도를 드러내야 한다.
- 권한 label은 resource 범위를 포함한다. 예를 들어 workflow `manager`는 `워크플로우 관리 가능`, LLM credential `manager`는 `Credential 관리 가능`처럼 표시한다.
- 권한 selector와 badge에는 짧은 tooltip을 붙여 organization manager와 resource manager 의미가 섞이지 않게 한다.
- revoke는 권한 회수 confirm을 띄운다. Team 권한 회수와 user direct 권한 회수를 label로 명확히 구분한다.
- workflow manager가 organization manager가 아닌 경우의 resource-level 관리 화면은 Admin Console과 분리한다. Admin Console은 organization manager 전용이다.

### LLM Credentials

목적: 조직에서 사용할 LLM credential을 등록하고 접근 권한을 관리한다.

주요 UI:

- provider별 credential list
- 등록 drawer/modal
- model sync action
- credential delete action
- credential permission 관리 진입

작업:

- credential 등록
- model sync
- credential 삭제
- credential별 team/user 접근 권한 부여

UI/UX 구현 기준:

- 등록은 drawer/modal에서 `provider`, `credential_name`, `api_key`를 입력받는다. API key는 저장 후 다시 보여주지 않는다는 안내를 form 하단에 둔다.
- provider별 list는 valid/invalid 상태, preview, model count를 보여준다. Sync 결과는 row 안의 temporary status로 표시한다.
- delete는 `삭제`보다 실제 동작에 맞춰 `비활성화` 또는 `사용 중지` label을 검토한다. 현재 API는 `is_valid=false` soft disable 성격이다.
- model sync는 row action으로 두고, 동기화 중/성공/실패 상태를 row 단위로 표시한다. 전체 목록 error로 띄우지 않는다.
- credential 권한 관리는 credential row에서 `권한 관리` action으로 진입하거나, 권한 탭에서 resource type을 `LLM Credential`로 선택하게 한다. 두 진입점 중 하나를 primary로 정하고 중복 form을 만들지 않는다.
- Member Settings에서는 credential 등록/삭제/sync를 노출하지 않는다. Manager action은 Admin Console로 이동한다.

### LLM Credential 권한 관리

목적: LLM credential에 대해 team/user direct 접근 권한을 관리한다.

주요 UI:

- credential selector 또는 credential row detail
- grantee type: team, user direct
- grantee picker: team list 또는 active organization member picker
- auth state selector
- team/user permission table

작업:

- team LLM credential permission grant/update/revoke
- user direct LLM credential permission grant/update/revoke

UI/UX 구현 기준:

- Workflow 권한 관리와 같은 패턴을 재사용한다. 다만 resource selector의 primary label은 credential name과 provider name을 함께 보여준다.
- credential read/write/manage 의미가 workflow viewer/operator/builder/manager와 다르게 느껴질 수 있으므로, badge tooltip 또는 짧은 help text로 권한 의미를 보강한다.
- LLM credential 권한 badge label은 workflow 권한 badge label과 분리한다. 같은 `manager`라도 `Credential 관리 가능`으로 표시한다.
- LLM credential 삭제 또는 invalid 상태일 때 권한 부여 action은 막고, 기존 권한 목록은 read-only로 확인 가능하게 둔다.
- organization manager override로 권한을 관리하는 경우와 credential manage 권한으로 관리하는 경우의 노출 차이는 후속 resource-level 관리 화면에서 분리한다.

### 지식 기반

목적: organization의 knowledge base와 접근 권한을 관리한다.

주요 UI:

- knowledge base list
- owner/관리자
- 연결 상태
- 문서 수 또는 sync 상태
- 권한 관리 action

작업 후보:

- knowledge base 생성/삭제 관리
- team/user 접근 권한 관리
- 문서 업로드/삭제 권한 관리

현재 API 계약이 부족한 경우 이 탭은 placeholder 또는 read-only list부터 시작한다. 구현 전에 API 문서와 실제 코드 기준을 다시 확인한다.

### 감사 로그

목적: 조직 운영 action의 이력을 추적한다.

주요 UI:

- 기간 필터
- actor 필터
- action type 필터
- target type 필터
- status 필터
- audit table

현재 연결 가능한 API가 `/users/me/audit-logs`뿐이면 탭 title 또는 body에서 "내 활동 로그"임을 명확히 표시한다. 조직 전체 audit API가 확정된 뒤 목표로 삼을 표시 이벤트:

- member 초대/정지/제거/권한 변경
- team 생성/수정/비활성화/member 변경
- workflow permission grant/revoke
- LLM credential 등록/삭제/sync/권한 변경
- knowledge base 권한 변경

### 조직 설정

목적: 조직 자체의 기본 정보를 관리한다.

MVP에서는 후순위로 둔다.

후보:

- 조직명
- manager 목록
- 기본 team
- member 제거/정지 정책 안내
- 위험 action 영역

## 권한별 접근

| 사용자 | Sidebar `관리` | `/dashboard/admin` 직접 접근 | Admin API 호출 |
| --- | --- | --- | --- |
| organization manager | 표시 | 허용 | 허용 |
| organization member | 숨김 | permission denied 또는 Settings/Home으로 redirect | manager-only API 호출하지 않음 |
| workflow manager only | 숨김. organization manager가 아니면 Admin Console 진입 불가 | 차단 | 조직 관리 API 호출하지 않음 |
| anonymous | 숨김 | login redirect | 호출하지 않음 |

Workflow manager는 특정 workflow의 권한 관리 권한을 가질 수 있지만 organization member list API를 읽을 수 없을 수 있다. Admin Console은 organization manager 전용으로 제한한다. Workflow manager 전용 permission UI가 필요하면 별도 resource-level 관리 화면으로 분리한다.

## 화면 상태

| 상태 | UI |
| --- | --- |
| loading | Admin shell header와 tab skeleton을 먼저 보여주고 tab body에 table skeleton |
| no active organization | 조직을 선택하거나 생성해야 한다는 empty state |
| member 접근 | 관리 권한 없음 안내. sidebar에는 메뉴 미노출. manager-only API는 호출하지 않음 |
| manager API 403 | active organization mismatch 또는 권한 변경 가능성을 안내하고 새로고침 제공 |
| empty members | 초대 CTA. 초대 UI flow가 아직 연결되지 않았으면 후속 구현 안내 |
| empty teams | team 생성 CTA |
| empty permissions | 선택한 resource에 부여된 권한 없음 |
| empty credentials | credential 등록 CTA |
| tab API error | 전체 Admin shell은 유지하고 해당 tab body만 error state |

부분 실패 기준:

- 탭의 핵심 목록 API가 실패하면 해당 tab body에 error state를 표시한다.
- 하위 row 보조 API만 실패하면 전체 tab을 막지 않는다. 예를 들어 team 목록은 성공했지만 특정 team member 조회가 실패하면 해당 team row에 "멤버를 불러오지 못함"을 표시한다.
- mutation 실패는 table 전체 error보다 action이 발생한 drawer, row, toast에 가깝게 표시한다.

## Layout 기준

- Admin Console은 card-heavy landing page가 아니라 운영 콘솔이어야 한다.
- 상단에는 조직명, manager badge, 간단한 상태 요약을 둔다.
- Tabs는 상단 고정 영역 아래에 배치하고, body는 table/list 중심으로 구성한다.
- destructive action은 row action menu 또는 detail drawer 안에 두고 확인 dialog를 사용한다.
- 생성/초대/등록처럼 입력이 필요한 action은 modal보다 drawer를 우선 검토한다. 단순 form이면 modal도 가능하다.
- Settings에서 Admin Console로 이동하는 CTA는 manager에게만 표시한다.
- Member에게는 Admin Console의 존재를 과하게 노출하지 않는다.

## 현재 코드 연결

| 파일 | 역할 |
| --- | --- |
| `apps/client/app/features/dashboard/components/Sidebar.tsx` | organization manager에게만 sidebar `관리` 메뉴 노출 |
| `apps/client/app/dashboard/admin/page.tsx` | Admin Console UI shell과 상단 탭 구현 |
| `apps/client/app/features/organization/api/organizationApi.ts` | organization current/member 목록 조회 |
| `apps/client/app/features/organization/components/MemberStateBadge.tsx` | member 상태 badge |
| `apps/client/app/features/organization/components/OrganizationAuthBadge.tsx` | manager/member badge |
| `apps/client/app/features/organization/components/ActiveOrganizationMemberPicker.tsx` | active organization member만 선택하는 공통 picker |
| `apps/client/lib/activeOrganization.ts` | active organization header 생성과 organization 변경 event 발행 |
| `apps/client/app/dashboard/settings/page.tsx` | Settings의 조직 운영 탭 제거, credential/activity read-only 중심 정리 |
| `apps/client/app/dashboard/page.tsx` | manager의 조직 접근 CTA를 Admin Console로 연결 |

현재 Admin Console은 멤버/팀 운영 action과 resource permission, LLM credential manager action을 실제 API에 연결한 상태다. Knowledge와 audit은 현재 확정 API 성격에 맞춰 read-only 또는 후속 범위로 둔다.

| 탭 | 현재 데이터 연결 |
| --- | --- |
| 멤버 | `GET /organizations/{id}/members` 기본 응답과 `state=removed` 조회 연결. 검색/필터/client pagination, user id 기반 초대, 정지/재활성화, manager 승격/member 강등, 제거와 cleanup summary 연결 |
| 팀 | `GET /teams`, `GET /teams/{id}/members` 실제 연결. 검색/필터/client pagination, 생성/수정, 비활성화, team detail drawer의 active member 추가/제거 연결 |
| 권한 | `/permissions/workflows/{workflow_id}`와 `/permissions/llm-credentials/{credential_id}` 목록/부여/회수 연결. Team permission과 user direct permission을 분리 표시 |
| LLM Credentials | provider/credential 목록, credential 등록, 삭제, model sync, credential 권한 관리 진입 연결 |
| 지식 기반 | 기존 knowledge base read list 실제 연결. Admin Console용 조직 관리 목록/권한 관리는 후속 API 확인 전까지 disabled |
| 감사 로그 | 현재는 `/users/me/audit-logs` 기반 read-only. 조직 전체 audit API 확정 후 전환 |
| 조직 설정 | 조직명 표시. 수정 action은 정책/API 확정 후 연결 |

## 현재 구현 스냅샷

현재 구현은 MBA-84 공통 organization member 기반 위에 멤버/팀 운영 action, workflow/credential 권한 관리, LLM credential manager action까지 연결한 상태다.

완료된 것:

- sidebar `관리` 메뉴는 active organization의 `is_manager`가 `true`인 경우에만 표시한다.
- `/dashboard/admin`은 manager guard를 먼저 수행하고, member에게는 manager-only API를 호출하지 않는다.
- Admin Console은 `멤버`, `팀`, `권한`, `LLM Credentials`, `지식 기반`, `감사 로그`, `조직 설정` 상단 탭을 제공한다.
- `멤버` 탭은 active/invited/suspended와 removed member를 표시하고, 검색/상태 필터/조직 권한 필터/client pagination을 제공한다.
- `멤버` 탭은 user id 기반 초대, 정지/재활성화, manager 승격/member 강등, 제거를 실제 API에 연결한다.
- `팀` 탭은 team list와 team member를 표시하고, 검색/상태 필터/member count 필터/client pagination을 제공한다. 현재 team list는 API `limit=100` 기준이며, 100개 이상일 가능성이 있으면 화면에 제한 안내를 표시한다.
- `팀` 탭은 team 생성/수정/비활성화와 detail drawer의 active member 추가/제거를 실제 API에 연결한다.
- `권한` 탭은 workflow와 LLM credential resource를 선택해 team/user direct 권한 목록을 조회하고, `PUT` upsert와 `DELETE` 회수를 수행한다.
- `LLM Credentials` 탭은 provider/credential 목록을 표시하고, credential 등록/삭제/model sync와 credential 권한 관리 진입을 제공한다.
- `지식 기반`, `감사 로그`는 기존 read API가 있는 범위만 연결한다.
- Settings는 `조직 접근` 탭을 노출하지 않고, `LLM Credentials`와 `Activity` 중심으로 남긴다.
- Settings의 LLM Credentials는 manager/member 모두 read-only이며 등록, 삭제, model sync, permission grant/revoke action을 제공하지 않는다.
- active organization 변경 시 sidebar와 Admin Console이 같은 event를 기준으로 다시 조회된다.
- organization member 공통 타입/API client/badge/picker 기반은 `apps/client/app/features/organization/` 아래에 분리되어 있다.

아직 구현하지 않은 것:

- email 또는 user directory 검색 기반 organization member 초대. 현재 초대 UI는 가입 user id 입력 방식으로만 연결한다.
- knowledge base organization-level 관리 action 연결
- organization 전체 audit API 전환
- team 목록의 서버 pagination. 현재는 API limit 100개 기준 client pagination이다.

주의할 점:

- `GET /organizations/{id}/members` query가 없으면 removed member는 응답에 포함되지 않는다. 제거된 member까지 보여주는 화면은 `state=removed` query 또는 별도 필터 동작이 필요하다.
- 현재 Admin Console의 제거 member count는 기본 member 응답과 `state=removed` 조회를 합친 클라이언트 로드 범위 기준이다. 서버 전체 total summary API가 아니므로 대규모 조직의 정확한 집계 API가 필요하면 별도 endpoint를 검토한다.
- Settings 내부에는 이전 `조직 접근` UI에 쓰이던 legacy branch/handler가 일부 남아 있을 수 있다. 현재 navigation에서는 접근되지 않지만, Admin Console mutation 전환이 완료되면 제거 범위를 다시 정리한다.
- Admin Console의 disabled action은 API가 없다는 뜻이 아니라, 현재 MVP action scope 밖이라는 뜻일 수 있다. 멤버/팀/action과 workflow/credential 권한, LLM credential manager action은 연결됐고, knowledge organization-level 관리와 organization audit은 후속 이슈에서 연결한다.

## 구현 순서 제안

| 순서 | 작업 |
| --- | --- |
| 1 | Sidebar에 manager-only `관리` 메뉴 추가 |
| 2 | `/dashboard/admin` shell 생성: organization header, manager guard, top tabs |
| 3 | 기존 Settings의 조직 접근 구현을 Admin Console `팀`/`권한` 탭으로 이동할 수 있게 컴포넌트 분리 |
| 4 | `멤버` 탭에 organization member list/read-only 상태 badge부터 구현 |
| 5 | `팀` 탭에 team list를 먼저 표시하고, active member picker는 후속 action 연결 시 추가 |
| 6 | `권한` 탭에 workflow permission grant/update/revoke 연결 |
| 7 | LLM Credentials manager action을 Settings에서 Admin Console로 이동 |
| 8 | 지식 기반/감사 로그는 API 준비 상태에 따라 placeholder 또는 read-only list부터 연결 |
| 9 | Settings는 개인 설정/read-only credential/activity 중심으로 정리 |

## MVP Action Scope

MBA-84 이후 바로 이어지는 manager 화면 구현은 범위를 좁힌다. 모든 탭의 mutation을 한 PR에 연결하지 않는다.

우선 구현:

- 멤버 목록 검색/필터/client pagination
- 멤버 초대 drawer
- 멤버 정지/재활성화
- manager 승격/member 강등
- 멤버 제거와 cleanup 결과 표시
- 팀 목록 검색/필터/client pagination
- 팀 생성/수정
- 팀 비활성화
- 팀 detail drawer에서 active member 추가/제거

후속 구현:

- 지식 기반 권한 관리
- 조직 전체 audit log 전환

이 범위는 QA 비용을 줄이기 위한 것이다. MBA-84에서 shell/read-only 기반을 만든 뒤, 현재는 연결 가능한 resource permission과 LLM credential action까지 Admin Console로 이동했다.

## QA 체크리스트

- [ ] manager에게 sidebar `관리`가 보인다.
- [ ] member에게 sidebar `관리`가 보이지 않는다.
- [ ] member가 `/dashboard/admin` 직접 접근 시 manager-only API를 호출하지 않는다.
- [ ] Admin Console 상단에 organization name과 `관리자` badge가 보인다.
- [ ] Admin Console 탭이 `멤버`, `팀`, `권한`, `LLM Credentials`, `지식 기반`, `감사 로그`, `조직 설정` 순서로 보인다.
- [ ] `멤버` 탭에서 invited/active/suspended/removed 상태가 구분된다.
- [ ] `멤버` 탭 검색/상태 필터/조직 권한 필터/client pagination이 동작한다.
- [ ] `멤버` 탭에서 user id 기반 초대가 동작하고, email 검색 초대가 아직 미지원임을 안내한다.
- [ ] `멤버` 탭에서 정지/재활성화, manager 승격/member 강등, 제거가 동작한다.
- [ ] `멤버` 제거 후 cleanup summary가 표시된다.
- [ ] 마지막 manager와 자기 자신에 대한 위험 action은 차단된다.
- [ ] `팀` 탭에서 team 검색/상태 필터/member count 필터/client pagination이 동작한다.
- [ ] 팀 목록이 100개에 도달하면 API limit 기준 안내가 보인다.
- [ ] `팀` 생성/수정/비활성화가 동작한다.
- [ ] team detail drawer에서 active member 추가/제거가 동작한다.
- [ ] 특정 team member 조회 실패가 tab 전체를 깨지 않고 해당 row/drawer에만 표시된다.
- [ ] `권한` 탭에서 workflow 권한 목록 조회, team/user direct 권한 저장과 회수가 동작한다.
- [ ] `권한` 탭에서 LLM credential 권한 목록 조회, team/user direct 권한 저장과 회수가 동작한다.
- [ ] LLM credential 등록/삭제/sync가 Admin Console에서 동작한다.
- [ ] tab 하나의 API 실패가 전체 Admin Console을 깨지 않는다.

## 남은 결정

| 항목 | 결정 필요 |
| --- | --- |
| `/dashboard/admin` 경로명 | `admin`, `manage`, `organization` 중 제품 용어 확정 |
| Settings의 기존 `조직 접근` 구현 제거 시점 | Admin Console 기능 전환 완료 후 내부 legacy 코드를 제거할지 |
| 지식 기반 권한 API 범위 | manager가 실제로 어떤 knowledge base action을 관리할 수 있는지 |
| 감사 로그 API 범위 | 조직 전체 audit endpoint가 있는지, 현재 `/users/me/audit-logs`만 쓸지 |
| workflow manager 전용 관리 화면 | Admin Console과 별도 resource-level 권한 관리 UI가 필요한지 |
