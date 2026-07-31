# Organization Component Spec

Status: Draft
Verified Against: feature/mba-127 @ 258b26a9

검증 값은 ActorAccessDrawer/confirm과 관련 organization API wrapper에 적용한다. 기존 organization 관리 UI의 나머지 섹션은 각 구현 이력의 기준을 따른다.

## Screens

- 스크린샷: 없음

### Dashboard Layout

- 출처: `apps/client/app/dashboard/layout.tsx`
- 경로: `/dashboard/*`
- 책임: dashboard 하위 화면을 렌더링하기 전에 active organization context를 준비한다.
- 구성:
  - `ActiveOrganizationGate`
  - `Sidebar`
  - dashboard child route content
- 시각 표면:
  - `/dashboard`, `/dashboard/mymodule`, `/dashboard/explore`, `/dashboard/statistics`, `/dashboard/knowledge`, `/dashboard/admin`, `/dashboard/settings`의 페이지 배경은 `rgb(255, 255, 255)`를 사용한다.
  - 최상위 페이지 제목은 `text-2xl`(24px)로 통일하고 제목 왼쪽에 해당 sidebar navigation과 같은 아이콘을 표시한다. 공용 `DashboardTitle`이 아이콘과 제목의 순서·간격을 담당한다.

### DashboardHomePage

- 출처: `apps/client/app/dashboard/page.tsx`
- 경로: `/dashboard`
- 책임: 현재 organization 이름, 내 organization 역할, workflow 접근 상태, manager용 team 요약을 조회해 표시한다.
- 현재 동작:
  - `/organizations`로 접근 가능한 organization 목록을 가져온다.
  - 저장된 active organization이 유효하면 사용하고, organization이 하나뿐이면 자동 선택한다.
  - `/organizations/current`로 현재 organization 상세를 가져온다.
  - manager면 `/teams`와 `/teams/{team_id}/members`를 조회해 team 현황을 표시한다.
  - organization state를 변경하지 않는다.

### AdminConsolePage

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 경로: `/dashboard/admin`
- 책임: organization manager가 organization member, team, workflow permission, Knowledge Base permission, LLM credential permission을 관리하는 주 UI다.
- 현재 동작:
  - `/organizations/current`와 `/auth/me`를 병렬로 조회한다.
  - manager가 아니면 `관리 권한 없음` 상태를 표시한다.
  - manager면 member/team/permission 데이터를 로드하고 tab UI로 관리한다.
  - LLM credential, knowledge, audit 관련 tab도 포함한다. MBA-176에서는 permission controls에 Knowledge Base team/user direct grant/revoke를 포함한다.
- MBA-141 목표 계약:
  - 상단의 `멤버`, `팀` tab을 `조직 구성` tab 하나로 통합한다.
  - `조직 구성` 안에서 `멤버`와 `팀` 보기를 전환하며 한 번에 선택한 목록 하나만 전체 너비로 렌더링한다.
  - 기본 보기는 `멤버`다.
  - canonical URL은 `?tab=organization-structure&view=members|teams`다.
  - 기존 `?tab=members`, `?tab=teams` deep link는 각각 대응하는 canonical URL로 정규화한다.
  - 별도 `조직 설정` 상위 tab은 제공하지 않는다. 기존 `?tab=organization` deep link는 `?tab=organization-structure&view=members`로 정규화한다.
  - member와 team의 검색, filter, pagination state는 서로 독립적으로 유지한다.

### SettingsPage

- 출처: `apps/client/app/dashboard/settings/page.tsx`
- 경로: `/dashboard/settings`
- 책임: 현재 organization 이름과 organization auth badge를 표시한다. Access-management tab을 노출하는 경우 AdminConsolePage와 같은 workflow/KB/LLM permission semantics를 사용해야 한다.
- 현재 동작:
  - organization manager에게 `Access`, `LLM Credentials` tab을 노출하고 일반 member에게 `LLM Credentials` tab만 노출한다.
  - organization auth badge는 `설정` 제목 바로 옆에 표시해 관리 화면의 page header와 위치를 맞춘다.
  - 감사 조회는 AdminConsolePage의 감사 로그 tab이 소유하며 SettingsPage는 Activity tab 또는 본인 audit-log 요청을 제공하지 않는다.
  - Settings access-management는 KB direct grant/revoke와 `none` 거부, DELETE revoke, active member prerequisite를 AdminConsolePage와 동일하게 구현한다.

### CreateAppModal Permission Request State

- 출처: `apps/client/app/features/app/components/create-app-modal/index.tsx`
- 경로: `/dashboard/mymodule`의 App 생성 모달
- 책임: App 생성 권한이 없는 사용자가 `POST /apps`에서 `403`을 받으면 권한 신청 사유 입력 UI로 전환한다.
- 현재 동작:
  - App 생성 API가 `403`을 반환하면 일반 실패 toast를 표시하지 않고 모달 제목을 `앱 생성 권한 신청`으로 바꾼다.
  - 사유 textarea를 표시하고, 앱 이름이 있으면 기본 신청 사유를 생성한다.
  - `권한 신청` 클릭 시 `organizationApi.submitPermissionRequest`로 `POST /permission-requests`를 호출한다.
  - 제출 성공 시 success toast를 표시하고 모달을 닫는다.
  - `409 Pending permission request already exists`는 중복 신청 안내 toast로 처리한다.
  - `409 App creation permission already granted`는 이미 권한이 있다는 안내 후 App 생성 입력 상태로 되돌린다.

## Components

### ActiveOrganizationGate

- 출처: `apps/client/app/features/dashboard/components/ActiveOrganizationGate.tsx`
- 책임: dashboard 진입 시 active organization을 확인하고 dashboard child를 렌더링할지, 선택 화면을 보여줄지 결정한다.
- 렌더링:
  - `loading`: 제목 `조직 확인 중`, 설명 `현재 작업할 조직을 확인하고 있습니다.`
  - `error`: 제목 `조직을 확인할 수 없습니다`, 오류 메시지
  - `select`: 제목 `작업 조직 선택`, organization 목록 버튼, manager 여부 라벨(`관리자`/`멤버`)
  - `ready`: child content
- 데이터:
  - `publicApiClient.get('/organizations')`
  - `moduly_active_organization_id` localStorage

### Sidebar

- 출처: `apps/client/app/features/dashboard/components/Sidebar.tsx`
- 책임: 현재 organization 이름과 manager 여부를 표시하고, manager-only navigation item을 제어한다.
- 렌더링:
  - 펼친 sidebar는 `232px`, 접힌 sidebar는 `80px` 너비를 사용한다.
  - organization 이름이 있으면 sidebar 하단에 organization switcher를 표시한다.
  - switcher는 현재 organization 이름과 `내 조직`/`멤버 조직` badge를 표시한다.
  - `내 조직`은 현재 MVP에서 `is_manager === true` 기준이다.
  - 접근 가능한 active organization이 2개 이상이면 switcher 클릭 시 dropdown을 열고 organization 목록을 표시한다.
  - dropdown item은 organization 이름, `내 조직`/`멤버 조직` badge, 현재 선택됨 상태를 표시한다.
  - 다른 organization item을 클릭하면 active organization을 저장하고 `/dashboard`로 이동한다.
  - organization이 1개뿐이면 switcher는 정보 표시만 하고 dropdown을 열지 않는다.
  - 펼친 sidebar의 `Nodease` brand header에는 장식 아이콘을 표시하지 않는다. 접힌 sidebar의 대시보드 홈 아이콘은 navigation affordance로 유지한다.
  - collapsed sidebar에서는 organization switcher를 표시하지 않는다.
  - `워크플로우 목록` navigation item은 노드 연결 흐름을 나타내는 `Workflow` 아이콘을 사용한다.
  - `isOrganizationManager`가 true일 때만 `관리` navigation item을 표시한다.
  - 사용자 프로필 드롭다운에는 `알림`, `로그아웃` action을 표시한다.
  - 조직 초대가 하나 이상 있거나 manager에게 열린 Security Alert가 하나 이상 있으면, 펼침 여부와 무관하게 프로필 원형 아이콘 우상단에 빨간 점을 표시한다. 시각적 점은 `aria-hidden`으로 숨기고 프로필 button 안의 `sr-only` 텍스트 `확인할 알림 있음`으로 상태를 전달한다. 두 source가 모두 비어 있으면 점과 텍스트를 숨긴다.
  - 같은 조직의 Security Alert summary를 background refresh할 때는 마지막 성공값을 유지한다. 성공 응답으로만 교체하며, 조직 전환 또는 권한 상실을 뜻하는 `403`에서 기존 값을 지운다.
  - `알림` 클릭 시 페이지 이동 없이 notification overlay를 연다.
- 데이터:
  - `authApi.me()`
  - `apiClient.get('/organizations')`
  - `apiClient.get('/organizations/current')`
  - `notificationsApi.listNotifications()`
  - organization owner/manager인 경우 `adminApi.getSecurityAlertSummary()`
  - `EventSource('/api/v1/notifications/stream')`
  - `nodease-active-organization-changed` window event

### NotificationOverlay

- 출처: `apps/client/app/features/notifications/components/NotificationOverlay.tsx`
- 책임: organization invitation과 manager-only Security Alert summary를 서로 다른 section으로 표시한다. Invitation action은 Organization feature가, Security Alert item/deep link는 [Security Alert component spec](../security-alert/component_spec.md)이 소유한다.
- Invitation 동작:
  - Sidebar mount 시 `GET /notifications`로 초기 알림 목록을 조회한다.
  - `notifications.changed` SSE event를 받으면 `GET /notifications`를 재조회한다.
  - `organization.invitation` item만 렌더링한다.
  - 각 item은 organization 이름, organization 권한(`member`/`manager`), 초대 시각, `수락`, `거절` 버튼을 표시한다.
  - `수락`은 `POST /organizations/{organization_id}/members/me/accept`, `거절`은 `POST /organizations/{organization_id}/members/me/decline`을 호출한다.
  - 성공 후 toast를 표시하고 알림 목록을 재조회한다.
  - Invitation이 없으면 invitation section의 empty state를 표시한다.
- Security Alert target 동작:
  - 현재 organization owner/manager만 `/admin/security-alerts/summary`를 조회하고 `보안 알림` section과 open badge를 표시한다.
  - Alert item은 severity, rule label, safe actor, occurrence count, 최근 시각을 표시하고 `/dashboard/admin?tab=security-alerts&alertId=<uuid>`로 이동한다.
  - Overlay 안에서 acknowledge/resolve를 수행하지 않는다.
  - Invitation과 Security Alert의 loading/error/empty 상태를 분리하고 한 source 실패로 다른 source를 숨기지 않는다.
  - Security Alert에는 별도 read/unread를 만들지 않고 open status만 badge로 센다.

### AdminShell

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: AdminConsolePage의 공통 page header, organization meta, refresh action, badge slot을 제공한다.
- 렌더링:
  - 제목 `관리`
  - 설명 `조직 멤버, 팀, 권한과 운영 리소스를 관리합니다.`
  - organization 이름 meta
  - refresh button

### OrganizationStructureTab (MBA-141 목표 계약)

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: organization member와 team 관리 진입점을 상위 `조직 구성` tab 하나로 묶고 내부 보기를 전환한다.
- 렌더링:
  - `멤버 <count>`와 `팀 <count>` 전환 button
  - count는 active 수가 아니라 각 API가 반환한 전체 member/team 목록 row 수다. active 수는 기존 상단 summary card가 별도로 표시한다.
  - 선택된 button의 명시적인 selected/pressed 상태
  - 선택한 보기의 `MembersTab` 또는 `TeamsTab` 하나
- 반응형:
  - 전환 button 묶음은 모든 화면에서 내용에 맞는 compact 너비를 사용한다.
  - 두 button은 같은 너비를 유지하고 아래 목록은 계속 전체 너비를 사용한다.
- URL 상태:
  - `tab=organization-structure&view=members`: member 보기
  - `tab=organization-structure&view=teams`: team 보기
  - `view`가 없거나 지원하지 않는 값이면 `members`를 기본값으로 사용한다.
  - legacy `tab=members|teams`는 의미가 같은 canonical URL로 교체한다.
  - 제거된 legacy `tab=organization`은 `tab=organization-structure&view=members`로 교체한다.
- 상태:
  - 보기 전환과 browser history 이동은 URL 상태를 따른다.
  - member/team 검색, filter, pagination은 각 보기별 state를 유지하며 다른 보기로 전환해도 초기화하지 않는다.
- 제한:
  - 상위 tab bar에 별도 `멤버`, `팀` 항목을 함께 노출하지 않는다.
  - 프론트 노출은 기존 manager gate를 유지하고 API가 최종 권한 경계다.

### MembersTab

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: `조직 구성`의 member 보기로서 organization member 목록을 검색/필터/페이지네이션하고 초대, 상태 변경, 권한 변경, 제거 action을 제공한다.
- 렌더링:
  - `멤버` panel
  - `초대` button
  - 이름/email 검색 input
  - membership state filter(`전체 상태`, `활성`, `초대 중`, `정지`, `제거`)
  - organization auth filter(`전체 권한`, `관리자`, `멤버`)
  - member table columns: 이름, 상태, 조직 권한, 비용 (USD), 초대, 수락, 작업
  - `MemberStateBadge`, `OrganizationAuthBadge`, `MemberActions`
- 비용:
  - `비용 (USD) ↓`는 이번 달 KST 기준의 `current_month_usage.total_cost`를 비용 내림차순으로 표시하고, 동률은 이름과 user id 순으로 안정적으로 정렬한다.
  - 총비용 아래에 비용 탭과 동일하게 `워크플로우 실행`과 `Agent Builder` 구분 비용을 표시한다.
  - membership state는 비용 표시의 조건이 아니다. invited, suspended, removed row도 해당 달에 사용자 usage가 있으면 실제 비용을 표시하며, usage가 없을 때만 `$0.00`을 표시한다.
- 제한:
  - removed member row는 action 대신 `제거됨`을 표시하지만, 과거 사용 기록에서 집계된 비용은 유지해 표시한다.
  - current user를 알 수 없으면 member action button을 비활성화한다.
  - 자기 자신이거나 마지막 active manager인 row는 `정지`, `강등`, `제거` button을 비활성화한다.

### MemberActions

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: member 상태/권한 변경과 제거 action button을 렌더링한다.
- 렌더링:
  - active member: `정지`
  - suspended member: `재활성화`
  - active member auth=`member`: `관리자 승격`
  - active member auth=`manager`: `멤버로 강등`
  - non-removed member: `제거`
- 상호작용:
  - organization_auth_state 변경과 제거는 AdminConsolePage confirm dialog를 거친다.

### ActorAccessDrawer

- 출처: `apps/client/app/features/admin/components/ActorAccessDrawer.tsx`.
- 책임: audit actor 또는 member row에서 current organization member의 membership, role, team, App 생성 권한, direct/team-inherited resource access를 user 중심으로 조회하고 항목별 관리 action을 제공한다.
- 표시: 기존 `text-xs/sm/base/lg` 계층을 각각 한 단계 키우고 drawer 최대 폭을
  `max-w-4xl`로 확장해 멤버십 4열 지표와 actor·team·resource 정보의 불필요한
  줄바꿈을 줄인다.
- 진입:
  - AuditSearchTab의 user actor button
  - 필요 시 MembersTab의 동일 member detail action
- 데이터 원천:
  - `GET /organizations/{organization_id}/members/{user_id}/access-profile`
  - `GET /organizations/{organization_id}/members/{user_id}/team-memberships`
  - `GET /organizations/{organization_id}/members/{user_id}/resource-access`
  - `POST /organizations/{organization_id}/members/{user_id}/access-actions`
- 렌더링:
  - user name/email, global user active state, membership state, organization role
  - effective access enabled/disabled 상태
  - manager override와 action별 allow/block reason. Role set은 desired `member`/`manager`별 control을 각각 표시
  - paginated active/inactive team membership 목록과 type별 inherited resource count. Inactive team은 effective source가 아닌 cleanup 대상으로 표시
  - App 생성 effective source와 별도의 stored direct row
  - resource type/source filter와 paginated resource access
  - direct permission과 team source를 분리한 auth state
- Catalog team/resource 선택 시 `teamId`/`resourceId` exact 조회로 다른 page의 existing row를 확인한 뒤 create/update precondition을 구성한다. Paginated current page만 보고 absence를 추론하지 않으며 exact 조회가 실패하면 추가/부여 confirm을 열지 않고 재조회 control을 제공한다.
- 제한:
  - ADR-0009의 organization manager 판정을 통과한 caller에게만 control을 제공한다.
  - system/null actor와 invited/removed/missing historical actor는 drawer를 열지 않고 audit detail만 유지한다.
  - active manager target은 role 강등 전 resource/team/App-creation mutation을 비활성화한다. Suspended manager role은 active override가 아니다.
  - suspended target은 stored grant를 표시하지만 role promotion/direct grant/team add/App-creation grant는 재활성화 후 제공한다. Manager-to-member 강등, existing direct/App revoke와 team remove는 cleanup 목적으로 허용한다.
  - globally deactivated target은 effective disabled와 cleanup-only 상태를 표시한다. Global account reactivation control은 제공하지 않는다.

### Actor Access Confirm Dialog

- 모든 suspend/reactivate, role, team, direct permission, App-creation action 전에 표시한다.
- Target user, action, resource/team, current/next state와 effective impact를 표시한다.
- Team membership remove confirm은 조회 snapshot의 workflow/Knowledge Base/LLM credential inherited resource source count를 표시하고 한 team 제거가 해당 team의 모든 current source에 영향을 준다고 명시한다. Count는 mutation precondition이 아니고 direct/다른 team source가 남을 수 있으므로 exact effective access loss 수로 표현하지 않는다. 응답의 transaction-observed source count와 재조회 결과로 완료 상태를 갱신한다.
- Optional reason textarea는 최대 500자이며 blank는 null로 전송한다.
- Client는 CRLF normalization 후 Unicode code point 기준으로 길이를 계산하고 forbidden control/bidi 문자를 제출하지 않는다. Server validation과 redaction이 최종 경계다.
- 한 confirm은 한 access action만 제출한다.
- 제출 payload는 drawer snapshot의 user-active/membership id/state/role과 action별 source row id/auth-state/absence precondition을 포함한다.
- 처리 중 confirm action을 비활성화한다. 성공 후 profile/source를 재조회하며, 409/404에서는 stale confirm payload를 폐기하고 dialog를 닫은 뒤 최신 profile/source를 재조회한다. 사용자가 새 snapshot에서 action을 다시 선택해야 하며 자동 재시도하지 않는다.
- Confirm dialog는 초기 focus, ESC 취소, 양방향 focus trap과 trigger focus 복귀를 제공하고 열린 동안 바깥 drawer를 inert/hidden 처리한다.
- Team membership action 뒤에는 profile count와 team-membership page를 함께 재조회한다.

### TeamsTab

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: `조직 구성`의 team 보기로서 team 목록을 검색/필터/페이지네이션하고 team 생성, 수정, 비활성화, 상세 side panel 진입을 제공한다.
- 렌더링:
  - `팀` panel
  - `팀 생성` button
  - team 이름/설명 검색 input
  - 상태 filter(`전체 상태`, `활성`, `비활성`)
  - member filter(`전체 멤버`, `멤버 있음`, `멤버 없음`)
  - team table columns: 팀, 상태, 멤버, 작업
  - API limit 100 경고
- 제한:
  - inactive team의 비활성화 button은 disabled다.

### Team Editor SidePanel

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: team 생성 또는 수정 form을 제공한다.
- 렌더링:
  - 제목 `팀 생성` 또는 `팀 수정`
  - `팀 이름` input
  - `설명` textarea
  - `신규 멤버 자동 추가` checkbox
  - cancel/submit action

### Team Detail SidePanel

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: selected team의 member 목록과 team member add/remove action을 제공한다.
- 렌더링:
  - team active/inactive badge
  - member count
  - active team이면 `ActiveOrganizationMemberPicker`와 `추가` button
  - inactive team이면 `비활성 팀에는 멤버를 추가할 수 없습니다.`
  - team member list와 remove button

### PermissionsTab

- 출처: `apps/client/app/dashboard/admin/page.tsx`
- 책임: workflow/Knowledge Base/LLM credential resource permission을 team 또는 user direct 대상으로 부여/회수한다.
- 렌더링:
  - resource type select(`Workflow`, `Knowledge Base`, `LLM Credential`)
  - resource select
  - grantee type select(`Team`, `User direct`)
  - active team select 또는 `ActiveOrganizationMemberPicker`
  - auth state select(`viewer`, `operator`, `builder`, `manager`) with resource-specific labels
  - `저장` button
  - `Team permissions` list
  - `User direct permissions` list
- 제한:
  - 선택 가능한 resource, active team, active member가 없으면 grant button이 disabled다.
  - Knowledge Base user direct grant는 `viewer`, `operator`, `builder`, `manager`만 허용하고 `none`은 DELETE revoke로 표현한다.
  - 조직 멤버십은 grant 대상 조건일 뿐 KB 사용 권한이 아니라는 상태/문구를 유지한다.

### AppCreatePermissionRequestForm

- 출처: `apps/client/app/features/organization/components/AppCreatePermissionRequestForm.tsx`
- 책임: App 생성 권한(`app.create`) 신청 폼을 제공한다 (ORG-REQ-048, ORG-REQ-049). CreateAppModal(app feature)이 App 생성 `403` 차단 시 이 컴포넌트로 전환한다.
- props:
  - `onCancel`: 신청을 중단하고 호출자(모달)의 이전 화면으로 돌아간다.
  - `onClose`: 모달을 닫는다.
- 렌더링:
  - 권한 없음 안내: `App 생성 권한이 없습니다.`와 권한 신청 유도 설명
  - `신청 사유` textarea (필수)
  - `권한 신청` submit button, `취소` button
  - 제출 성공 시 신청 완료 안내와 `닫기` button
  - 서버 409 응답 시 이미 권한 보유/pending 중복에 맞는 안내
  - 실패 시 inline error 메시지
- 제한:
  - 신청 권한은 `app.create`로 고정하며 사용자가 다른 권한을 선택할 수 없다.
  - blank 사유는 제출 전에 차단하고 API를 호출하지 않는다.
  - 신청 사유에 secret/token/credential 원문을 넣지 않도록 안내하는 것은 UX 범위가 아니며, 서버/문서 정책으로 다룬다.

### permissionRequestApi

- 출처: `apps/client/app/features/organization/api/permissionRequestApi.ts`
- 책임: 권한 신청 API wrapper를 제공한다.
- 호출:
  - `submitPermissionRequest(payload)`: `POST /permission-requests`, body `{ requested_permission: 'app.create', reason }`
- 오류 해석 helper:
  - `409` + `detail="App creation permission already granted"`: 이미 권한 보유
  - `409` + `detail="Pending permission request already exists"`: pending 신청 중복

### ActiveOrganizationMemberPicker

- 출처: `apps/client/app/features/organization/components/ActiveOrganizationMemberPicker.tsx`
- 책임: active organization members 중 제외 대상이 아닌 user를 select option으로 제공한다.
- props:
  - `members`, `value`, `onChange`
  - `excludedUserIds`
  - `disabled`
  - `placeholder`, `emptyLabel`, `className`
- 렌더링:
  - active member가 없으면 `emptyLabel` option을 표시하고 select를 disabled한다.
  - option label은 `{user_name} ({user_email})`이다.

### OrganizationAuthBadge

- 출처: `apps/client/app/features/organization/components/OrganizationAuthBadge.tsx`
- 책임: organization auth state를 badge로 표시한다.
- 렌더링:
  - `manager`: `관리자`, blue tone
  - `member`: `멤버`, gray tone

### MemberStateBadge

- 출처: `apps/client/app/features/organization/components/MemberStateBadge.tsx`
- 책임: membership state를 badge로 표시한다.
- 렌더링:
  - `invited`: `초대 중`, amber tone
  - `active`: `활성`, green tone
  - `suspended`: `정지`, red tone
  - `removed`: `제거됨`, gray tone

### organizationApi

- 출처: `apps/client/app/features/organization/api/organizationApi.ts`
- 책임: organization context, member, 권한 신청 API wrapper를 제공한다.
- 호출:
  - `listOrganizations()`: `GET /organizations`
  - `listMemberships()`: `GET /organizations/memberships`
  - `getCurrentOrganization()`: `GET /organizations/current`
  - `listMembers(organizationId, state?)`: `GET /organizations/{id}/members`
  - `inviteMember(organizationId, payload)`: `POST /organizations/{id}/members/invitations`
  - `acceptInvitation(organizationId)`: `POST /organizations/{id}/members/me/accept`
  - `updateMember(organizationId, userId, payload)`: `PATCH /organizations/{id}/members/{user_id}`
  - `removeMember(organizationId, userId)`: `DELETE /organizations/{id}/members/{user_id}`
  - `submitPermissionRequest(payload)`: `POST /permission-requests`

### activeOrganization Helpers

- 출처: `apps/client/lib/activeOrganization.ts`
- 책임: client-side active organization id persistence와 request header attachment를 제공한다.
- 저장소:
  - key: `moduly_active_organization_id`
  - change event: `nodease-active-organization-changed`
- 함수:
  - `getStoredActiveOrganizationId`
  - `setActiveOrganizationId`
  - `resolveActiveOrganizationId`
  - `activeOrganizationHeaders`
  - `attachActiveOrganizationHeader`

## States

### ActiveOrganizationGate

- 초기 상태: `{ status: 'loading' }`
- ready 상태:
  - 저장된 organization id가 `/organizations` 응답 안에 있거나, 접근 가능한 organization이 하나뿐일 때 진입한다.
  - children을 렌더링한다.
- select 상태:
  - 접근 가능한 organization이 두 개 이상이고 저장된 organization id가 없거나 유효하지 않을 때 진입한다.
  - organization 선택 button 목록을 렌더링한다.
- error 상태:
  - organization 목록 응답이 배열이 아니거나 접근 가능한 organization이 없거나 요청이 실패할 때 진입한다.

### Sidebar

- user state:
  - `userName` 기본값은 `사용자`
  - `userEmail` 기본값은 빈 문자열
- organization state:
  - stored active organization id가 없으면 organization name을 비우고 manager flag를 false로 둔다.
  - `/organizations/current` 성공 시 name과 `is_manager`를 반영한다.
  - 조회 실패 시 name을 비우고 manager flag를 false로 둔다.
- dropdown state:
  - user footer click으로 logout dropdown을 열고 닫는다.
- notification state:
  - Invitation 목록과 Security Alert summary의 loading/error/data를 source별로 분리한다.
  - manager가 아니면 Security Alert summary를 요청하거나 이전 manager scope의 badge/cache를 유지하지 않는다.

### AdminConsolePage

- loading 상태:
  - `관리 콘솔을 불러오는 중...` loading block을 표시한다.
- non-manager 상태:
  - organization이 있고 `is_manager=false`이면 `관리 권한 없음` panel을 표시하고 관리 데이터를 로드하지 않는다.
- data 상태:
  - organization 관리에 필요한 `members`, `teams`, `teamMembers`, `workflowPermissions`, `credentialPermissions`를 저장한다.
  - 같은 page state에는 다른 feature tab 데이터도 존재하지만, 각 tab의 상세 책임은 해당 feature 문서가 소유한다.
  - organization member 목록은 기본 member list와 removed member list를 합쳐 중복 제거한다.
- tab 상태:
  - 이 문서의 책임 tab은 `members`, `teams`, `permissions`, `organization`이다.
  - 같은 화면에는 `credentials`, `knowledge`, `audit` tab도 있지만 상세 동작은 각 feature 문서가 소유한다.
- member filter state:
  - query, membership state, organization auth state, page
- team filter state:
  - query, active/inactive, member count filter, page
- panel state:
  - invite side panel
  - team editor side panel
  - team detail side panel
  - confirm dialog
  - credential panel
- notice/error state:
  - member removal cleanup summary를 notice와 toast로 표시한다.
  - API 실패는 toast 또는 inline error로 표시한다.

### AppCreatePermissionRequestForm

- form 상태: `idle`, `submitting`, `submitted`, `error`
- `idle`: 신청 사유 입력을 받는다. blank 사유로 제출하면 `신청 사유를 입력해주세요.` 안내를 표시하고 API를 호출하지 않는다.
- `submitting`: submit button을 비활성화한다.
- `submitted`: `201` 응답 후 신청 완료 안내를 표시하고 form을 숨긴다.
- `error`:
  - `409` 이미 권한 보유: `이미 App 생성 권한이 있습니다.` 안내를 표시한다.
  - `409` pending 중복: `이미 처리 대기 중인 신청이 있습니다.` 안내를 표시한다.
  - 그 외 실패: 일반 신청 실패 메시지를 표시하고 재시도를 허용한다.

### CreateAppModal (app feature 소유, 권한 신청 연결 지점)

- view 상태: `create`, `permission-request`
- `create` view에서 `POST /apps`가 `403`으로 실패하면 일반 실패 토스트를 표시하지 않고 `permission-request` view로 전환한다.
- `403`이 아닌 App 생성 실패(duplicate name `400`, 그 외 오류)는 기존 실패 토스트 처리를 유지한다.
- `permission-request` view는 `AppCreatePermissionRequestForm`을 렌더링한다.

### SettingsPage

- loading/error state를 가진다.
- visible tab은 organization manager의 `access`, `credentials`와 일반 member의 `credentials`이다. `activity` tab은 제공하지 않는다.
- organization name과 auth badge를 표시한다.
- `access` branch는 AdminConsolePage와 같은 workflow/KB/LLM permission list/grant/revoke contract를 사용한다.
- 감사 로그 조회와 표시는 AdminConsolePage가 소유하며 SettingsPage는 `/users/me/audit-logs`를 요청하지 않는다.

### CreateAppModal Permission Request State

- 기본 상태는 App 이름/설명/아이콘 입력 form이다.
- `POST /apps`가 `403`이면 `showPermissionRequest=true`가 되고 신청 사유 form을 표시한다.
- 신청 제출 중에는 신청 버튼과 `앱 정보 수정` 버튼을 비활성화한다.
- 신청 성공 후에는 부모 성공 콜백을 호출하지 않고 모달만 닫는다. App은 아직 생성되지 않았기 때문이다.

## Interactions

### Active Organization Resolution

- Dashboard 진입 시 `ActiveOrganizationGate`가 `/organizations`를 호출한다.
- 저장된 organization id가 응답 목록에 있으면 그대로 사용한다.
- organization이 하나뿐이면 `setActiveOrganizationId`로 저장하고 ready 상태로 전환한다.
- organization이 여러 개면 select 상태를 보여주고, 사용자가 organization button을 클릭하면 localStorage를 갱신하고 ready 상태로 전환한다.
- `setActiveOrganizationId`는 `nodease-active-organization-changed` event를 dispatch한다.
- `apiClient` request interceptor는 `X-Organization-Id` header가 없는 요청에 저장된 organization id를 첨부한다.

### Dashboard And Sidebar

- DashboardHomePage는 active organization을 resolve한 뒤 `/organizations/current`, `/apps`, workflow permission summary를 조회한다.
- manager는 team summary를 볼 수 있고 `조직 접근 관리` button으로 `/dashboard/admin`에 이동한다.
- Sidebar는 active organization changed event를 받으면 `/organizations/current`를 다시 조회한다.
- Sidebar는 `isOrganizationManager`가 false면 `관리` nav item을 렌더링하지 않는다.
- Sidebar는 owner/manager일 때만 Security Alert summary를 조회한다. Active organization 변경 또는 manager 권한 회수 시 이전 organization alert summary와 badge를 즉시 제거한다.
- `notifications.changed`를 받으면 invitation 목록과, 권한이 있으면 Security Alert summary를 각각 재조회한다. Event payload를 두 source의 state로 직접 사용하지 않는다.

### Member Management

- AdminConsolePage는 manager일 때 `/organizations/{id}/members`와 `state=removed` 요청을 함께 실행한다.
- `초대` button은 invite side panel을 연다.
- 초대 side panel은 user UUID와 organization auth state를 입력받아 `organizationApi.inviteMember`를 호출한다.
- member state update는 `organizationApi.updateMember`를 호출한다.
- organization auth state 변경은 confirm dialog를 거친다.
- member removal은 confirm dialog를 거쳐 `organizationApi.removeMember`를 호출하고 cleanup count를 notice/toast로 표시한다.
- MBA-188 이후 audit actor/member detail은 ActorAccessDrawer를 재사용한다. Drawer mutation은 기존 member/team/permission 의미를 재정의하지 않고 access-management use case를 호출한다.

### Actor Access Management

- Audit actor button은 audit row click propagation을 중단하고 ActorAccessDrawer를 연다.
- ActorAccessDrawer는 profile을 먼저 조회한 뒤 resource tab/filter 선택 시 paginated resource access를 조회한다.
- Direct permission revoke 이후 team source가 남아 있으면 effective auth state가 유지됨을 결과에 반영한다.
- Team membership 제거 confirm에는 해당 team에서 파생되는 여러 resource access가 함께 사라질 수 있음을 표시한다.
- Direct permission restore는 이전 audit 값을 자동 선택하지 않고 manager가 resource와 canonical auth state를 선택한다.
- Actor direct grant picker는 `viewer`, `operator`, `builder`, `manager`만 제공하고 `none`은 revoke action으로 표현한다. Legacy none row는 inert source로 표시하고 revoke할 수 있다.
- Restore/grant resource picker는 기존 organization-scoped workflow/Knowledge Base/LLM credential catalog를 사용한다. Resource access source 응답은 현재 source가 있는 resource만 반환하므로 전체 catalog로 사용하지 않는다.
- Mutation 성공 후 actor profile/resource access/audit detail을 필요한 범위에서 재조회한다. Client state를 authorization source로 사용하지 않는다.
- Desired-state unchanged도 profile을 재조회한다. `stale_state`는 최신 profile로 갱신한 뒤 사용자가 다시 확인하게 하며 자동 재시도하지 않는다.

### Team Management

- AdminConsolePage는 `/teams?limit=100`을 조회하고 각 team의 `/teams/{team_id}/members`를 조회한다.
- `팀 생성`은 team editor side panel을 열고 `POST /teams`를 호출한다.
- team edit은 같은 side panel에서 `PATCH /teams/{team_id}`를 호출한다.
- team deactivate는 confirm dialog를 거쳐 `DELETE /teams/{team_id}`를 호출한다.
- team detail side panel은 active team일 때만 member add control을 표시한다.
- team member add는 `POST /teams/{team_id}/members`, remove는 `DELETE /teams/{team_id}/members/{user_id}`를 호출한다.

### Permission Management

- Permission tab은 selected resource type에 따라 `/permissions/workflows/{workflow_id}` 또는 `/permissions/llm-credentials/{credential_id}`를 조회한다.
- Permission tab 본문은 현재 조회 중인 resource type/name을 filter chip으로 표시한다.
- `리소스 필터 변경`은 아래로 펼쳐지는 영역에서 resource type과 이름 검색을 조합하고 결과를 선택하게 한다.
- 본문 filter의 resource 선택은 권한 변경 작업이 아니라 조회 대상 변경이므로 별도 저장 없이 즉시 permission 목록을 다시 불러온다.
- 기존 권한 회수는 본문 filter로 resource를 선택한 뒤 permission row의 `회수` action으로 수행한다. 새 권한을 저장할 필요가 없다.
- `권한 부여` button은 AWS Console 스타일의 modal을 연다. Modal은 resource table, team/user table, permission radio group을 순서대로 제공한다.
- Permission card에는 `권한 부여` action만 둔다. Selected resource 영역에 같은 modal을 여는 중복 action을 두지 않는다.
- Resource와 grantee는 하나의 resource/grantee type 안에서 각각 하나 이상을 checkbox로 다중 선택한다. 선택한 Cartesian product는 최대 50건이며 초과 시 저장 action을 비활성화한다.
- Resource와 grantee table은 결과가 많아도 modal 전체를 밀어내지 않도록 각각 약 10개 row가 보이는 최대 높이 500px의 독립 scroll 영역을 사용하고 table header를 상단에 고정한다.
- Modal의 resource type/resource/grantee/auth state는 draft state다. 선택 또는 취소만으로 바깥 permission card의 selected resource와 permission 목록을 변경하지 않는다.
- Bulk grant POST 성공 후에만 첫 번째 선택 resource/grantee를 page selection에 반영하고 해당 resource의 permission 목록을 조회한다. 실패하면 modal과 기존 page selection을 유지한다.
- Bulk grant POST 처리 중에는 modal에 busy 상태를 표시하고 배경/X/취소/Escape 닫기와 resource/grantee/auth state 입력을 모두 비활성화한다.
- Modal 밖의 permission card는 현재 selected resource와 기존 team/user permission 목록을 표시한다.
- grantee type이 team이면 active team select를 사용한다.
- grantee type이 user이면 `ActiveOrganizationMemberPicker`로 active member만 선택하게 한다.
- grant/save는 `POST /permissions/bulk-grants`를 호출한다.
- revoke는 confirm dialog를 거쳐 DELETE permission endpoint를 호출한다.
- LLM credential tab에서 permission tab으로 전달된 selected credential id가 있으면 permission tab의 resource selection에 반영한다.

### App Creation Permission Request

- `/dashboard/mymodule`의 `새 워크플로우` button은 CreateAppModal을 연다.
- CreateAppModal에서 `POST /apps`가 `403 permission.denied`로 차단되면(현재 구현 응답 본문은 `{"detail": "Forbidden"}`이며, 클라이언트는 `POST /apps`의 `403` status를 권한 없음으로 판정한다) modal이 권한 신청 view로 전환된다 (ORG-REQ-048).
- 권한 신청 view는 신청 사유를 입력받아 `permissionRequestApi.submitPermissionRequest`로 `POST /permission-requests`를 호출한다. 신청 권한은 `app.create`로 고정한다 (ORG-REQ-049).
- `201` 성공 시 신청 완료 안내를 표시한다. 사용자는 관리자 승인 후 다시 `새 워크플로우`를 시도한다 (PRD 신입 사용자 시나리오).
- `409` 응답은 `detail` 문자열로 이미 권한 보유와 pending 중복을 구분해 안내한다 (ORG-REQ-050).
- validation 실패와 그 외 오류는 form 안에 inline error로 표시한다.
- `취소`는 App 생성 입력 view로 돌아가고, 신청 완료 후 `닫기`는 modal을 닫는다.

### Settings Page

- SettingsPage는 `/organizations`와 `/organizations/current`로 organization context를 확인하고 organization name/auth badge를 표시한다.
- visible settings tab에서는 organization member/team mutation을 제공하지 않는다.

### App Creation Permission Request

- 사용자가 `CreateAppModal`에서 App 생성을 시도한다.
- `appApi.createApp`이 성공하면 기존처럼 목록 갱신, 모달 닫기, workflow editor 이동을 수행한다.
- `appApi.createApp`이 `403`을 반환하면 같은 모달 안에서 권한 신청 사유 입력 상태로 전환한다.
- 사용자가 사유를 입력하고 `권한 신청`을 누르면 `POST /permission-requests`에 `{ requested_permission: "app.create", reason }`를 보낸다.
- 제출 성공 후 관리자는 AdminConsolePage의 `권한 신청` 탭에서 승인/거절한다.

## Accessibility

- ActiveOrganizationGate의 organization 선택은 `<button>` 요소로 구현되어 키보드 조작이 가능하다.
- Sidebar nav는 Next `Link`를 사용하고, collapse button은 `<button>` 요소다. collapse button에는 `aria-expanded`가 없다.
- Sidebar collapsed logo button에는 `aria-label="대시보드 홈"`이 있다.
- Admin tab navigation은 `<button>` 요소로 구현되어 키보드 focus가 가능하지만 `role="tablist"`/`role="tab"` 속성은 없다.
- MembersTab과 TeamsTab은 table markup을 사용하고 header cell을 제공한다.
- Member/team/permission destructive actions는 confirm dialog를 거치지만, 현재 confirm dialog와 side panel에 명시적인 `role="dialog"`/`aria-modal` 연결은 확인되지 않는다.
- ActorAccessDrawer와 confirm dialog는 `role="dialog"`, `aria-modal`, focus trap, ESC/overlay close를 제공하고 닫힌 뒤 actor button으로 focus를 복원한다.
- Actor button은 native button 또는 link semantics를 사용하고 row click과 분리된 keyboard action을 제공한다.
- icon-only buttons 일부는 `title`을 제공한다.
- `ActiveOrganizationMemberPicker`는 native `<select>`를 사용한다. 별도 `<label>`은 호출자가 제공해야 한다.
- inline error/notice blocks는 시각적으로 구분되지만 `role="alert"`나 `aria-live`는 확인되지 않는다.
- CreateAppModal은 `role="dialog"`와 `aria-modal="true"`를 제공하며, 권한 신청 상태도 같은 dialog 안에서 표시된다.
- AppCreatePermissionRequestForm의 신청 사유 textarea는 `<label>`과 연결하고, 완료/오류 안내는 텍스트로 렌더링해 스크린 리더가 읽을 수 있어야 한다.
