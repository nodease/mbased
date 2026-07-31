# Admin Dashboard Component Spec

Status: Draft
검증 값은 MBA-188 actor access, audit detail 연동 섹션에 적용한다. 기존 비용/권한 신청 섹션의 기준은 해당 feature 문서와 git history를 따른다.

기존 관리자 페이지 `/dashboard/admin`(`apps/client/app/dashboard/admin/page.tsx`)을 확장한다. 이 페이지는 관리 tab 구조와 공용 컴포넌트(`DashboardPageHeader`, `DashboardPanel`, `DashboardSummaryCard`)를 갖고 있다. MBA-141 목표 계약은 기존 `멤버`/`팀` 상위 tab을 `조직 구성` 하나로 통합하고 내부 `view=members|teams`로 전환한다. 이 feature는 새 화면을 만들지 않고 다음을 추가/전환한다.

- 감사 로그 탭을 본인 이력(`/users/me/audit-logs`) 임시 구현에서 조직 단위 검색(`GET /admin/audit-logs`, FR-011)으로 전환
- 기존 `권한` 탭에 권한 신청·App 생성 권한 보유 카드를 통합 (FR-014)
- `비용` 탭 신설 (FR-012)
- 상단 요약 카드 신설 (FR-015)
- `보안 알림` 탭과 Sidebar deep link 연동 (FR-013, [Security Alert component spec](../security-alert/component_spec.md))

신청자 측 차단 팝업과 권한 신청 제출 폼(PRD FR-041)은 [organization](../organization/component_spec.md) 범위(`내 워크플로우` 화면)이며 이 문서에 포함하지 않는다.

## Screens

### `/dashboard/admin`

| 영역 | 내용 | 노출 조건 |
| --- | --- | --- |
| 상단 요약 카드 | 이번 달 조직 LLM 비용(USD), 예산 위험/초과 workflow 비율 | organization owner/manager |
| 감사 로그 탭 | 조직 audit log 검색/필터 + 상세 드로어 | audit `auditor` 이상 |
| Actor access drawer | Audit actor의 current organization access 조회/관리 | ADR-0009 organization manager |
| 권한 탭 | resource 권한 관리 + 권한 신청 목록/처리 + App 생성 권한 보유 목록/회수 | organization owner/manager |
| 비용 탭 | workflow별 사용량/비용 집계 | organization owner/manager |
| 보안 알림 탭 | 영속 Security Alert 검색·상세·safe evidence·상태 변경 | organization owner/manager |
| 조직 구성 탭 | member/team 내부 보기 전환. 상세 계약은 [organization component spec](../organization/component_spec.md)의 MBA-141 목표 계약을 따른다 | organization owner/manager |
| 기존 탭들 (권한/credential/knowledge) | 이 feature의 감사/비용/권한신청 범위 밖에서는 기존 구현 유지. MBA-176은 기존 권한/knowledge 탭을 확장해 KB team/user direct permission 관리를 추가한다 | 기존 기준 유지 |

`보안 알림` 탭은 `감사 로그` 앞에 두고 내부 key `security-alerts`를 사용한다. `/dashboard/admin?tab=security-alerts&alertId=<uuid>` deep link, 목록/filter/detail drawer, resolve dialog, 기존 ActorAccessDrawer handoff의 상세 계약은 [Security Alert component spec](../security-alert/component_spec.md)이 소유한다. Security Alert detail과 ActorAccessDrawer는 동시에 열지 않으며, 사용자 접근 조치 성공만으로 alert를 자동 resolve하지 않는다.

선택된 상위 탭은 설정 화면과 동일하게 `border-blue-600 text-blue-600`으로 파란 글자와 파란 밑줄을 표시한다. 선택되지 않은 탭은 기존 중립 색상을 유지한다.

별도 `조직 설정` 상위 탭은 제공하지 않는다. 기존 `/dashboard/admin?tab=organization` 주소는 `/dashboard/admin?tab=organization-structure&view=members`로 정규화한다. `/dashboard/settings`의 Access/LLM Credentials 화면은 이 변경과 무관하게 유지한다.

- (후순위) `auditor`/`raw_auditor` 전용 사용자에게는 감사 로그 탭만 노출하고 기본 탭을 감사 로그로 한다. 요약 카드와 나머지 탭은 렌더링하지 않는다. 현재 데모 시나리오에서 auditor 전용 계정을 사용하지 않으므로 이 노출 제어는 후순위로 미룬다. 구현 전까지 admin 페이지 접근은 기존 organization manager 게이트를 유지한다.
- 프론트 노출 제어는 UX 보조이며 최종 차단은 Gateway가 수행한다 (NFR-001). 권한 없는 API 응답(403)은 안내 문구로 처리한다. auditor 전용 노출 제어가 후순위인 동안에도 이 서버 경계는 그대로 적용된다.

## Components

### AdminSummaryCards (FR-015)

- 상단 요약은 `비용·예산`, `조직 구성`, `운영 리소스` 3장으로 묶는다. 데스크톱에서는 3열 한 줄, 모바일에서는 1열로 배치한다.
- `비용·예산` 카드는 이번 달 LLM 비용과 예산 위험 상태를 한 카드에 표시한다. 비용은 USD 소수점 2자리로 표시하고 `text-2xl` 크기를 사용한다 (표시 직전 1회 반올림).
- 예산 카드가 의존하는 판정/분모는 [budget-management](../budget-management/requirements.md)(PRD FR-051)를 따른다.
- API의 `budget` 블록이 있으면 대표 상태는 비율이 아니라 `at_risk_count + exceeded_count`를 계산한 `<n>개 위험`으로 표시한다.
- 상태 요약은 노란색 점과 `예산 임박 <at_risk_count>`, 빨간색 점과 `예산 초과 <exceeded_count>` 텍스트를 함께 사용한다. 의미를 색상만으로 전달하지 않는다.
- 활성 예산은 있지만 위험/초과 workflow가 0개면 `<0개 위험>`과 상태별 0건을 표시한다.
- API의 `budget` 블록이 null이면 비용 값 옆에 "예산 미설정" 상태를 표시한다.
- `비용·예산` 카드 전체를 클릭하면 `/dashboard/admin?tab=usage`로 이동한다. 총비용을 주 값으로 유지하고 같은 카드 안에 `워크플로 실행`과 `Agent Builder` 비용을 표시한다.
- `조직 구성` 카드 안의 활성 멤버/활성 팀 지표는 각각 `/dashboard/admin?tab=organization-structure&view=members`와 `view=teams`로 이동한다.
- `운영 리소스` 카드 안의 LLM Credentials/지식 기반 지표는 각각 `/dashboard/admin?tab=credentials`와 `/dashboard/admin?tab=knowledge`로 이동한다.
- 모든 링크는 별도 링크 문구 없이 접근 가능한 이름과 키보드 포커스 표시를 제공한다. 하나의 그룹 카드 안에 여러 목적지가 있으면 각 지표를 독립 링크로 렌더링한다.
- 요약 조회 중에는 기존 집계 중 상태를 유지하고, 실패하면 "요약을 불러오지 못했습니다"를 표시한다.
- 비용·예산 데이터 원천은 `GET /admin/summary`다. 조직 구성과 운영 리소스는 관리자 페이지가 이미 조회한 멤버, 팀, credential, provider, 지식 기반 집계를 사용한다.

### AuditSearchTab (FR-011)

- 필터 바: 행위자(기존 `ActiveOrganizationMemberPicker` 재사용), action(canonical action 문자열 입력/선택), 대상 타입/ID, 기간(`startAt`/`endAt`, KST 기준 입력), status. 필터 초기화 버튼을 둔다.
- 결과 테이블 컬럼: 발생 시각(사용자 로컬 시간대 렌더링), 행위자, action(사용자 친화 라벨 병기 — canonical action에서 파생), 대상, status 배지.
- 행위자와 대상은 safe display label을 먼저 표시하고 canonical UUID를 보조 text와 복사 가능한 값으로 병기한다. Display가 없으면 UUID만 표시한다.
- Pagination: Audit 목록은 opaque cursor history를 사용해 이전/다음 이동을 제공한다. 첫 페이지 응답의 total snapshot을 후속 cursor 페이지에서도 유지하고, 후속 응답의 `total=null`은 기존 값을 덮어쓰지 않는다. 필터를 새로 적용하거나 초기화하면 cursor history를 비우고 첫 페이지부터 조회해 total을 갱신한다.
- 행 클릭 → `AuditDetailDrawer` 열림.
- User actor cell은 별도 button으로 렌더링한다. Organization manager가 active/suspended current organization member actor를 선택하면 row click propagation을 중단하고 `ActorAccessDrawer`를 연다.
- Auditor-only admin page 노출은 기존 후순위 범위를 유지한다. 이후 해당 page가 열리더라도 auditor-only, system/null actor, invited/removed/missing historical actor에는 actor management control을 제공하지 않고 audit row/detail 동작만 유지한다.
- 데이터 원천: `GET /admin/audit-logs`.

### AuditDetailDrawer (FR-011)

- 화면 오른쪽 사이드 드로어. 목록 맥락을 유지한 채 상세를 보여준다.
- `text-xs/sm/base/lg` 계층을 각각 한 단계 키우고 최대 폭은 `max-w-xl`로 확장해
  actor 표시명·이메일·권한 문장이 좁은 폭 때문에 불필요하게 줄바꿈되지 않게 한다.
- 표시 필드: actor, action(canonical 문자열과 파생 라벨), target, status, timestamp, allowlist metadata(`request_id`, sanitized `reason`, `requested_action`, `policy_reason` 등).
- Actor/target과 `resolved_references`가 있는 allowlisted metadata/change summary UUID는 표시명을 먼저, UUID를 보조값으로 함께 표시한다. Resolver 실패는 상세 전체 오류가 아니라 ID-only fallback이다.
- Security Alert 관리자 API 권한 거부는 safe metadata를 이용해 시도한 작업과 조직 관리자 권한 필요 사유를 사용자 문장과 한국어 라벨로 먼저 표시한다. 기존 기록처럼 정보가 없으면 일반 접근 거부 설명을 표시한다.
- Supported access-management event는 target/action allowlist 기반 `change_summary.before/after`를 표시한다. Unknown target/action은 변경 요약 영역을 표시하지 않는다.
- raw payload, secret 계열 값은 표시하지 않는다 (NFR-004). raw payload 접근 UI는 이 feature 범위가 아니다 (trace visibility policy).
- 데이터 원천: `GET /admin/audit-logs/{id}`.

### ActorAccessDrawer (FR-016, FR-017)

- 오른쪽 side drawer로 audit list 맥락을 유지한다.
- `text-xs/sm/base/lg` 계층을 각각 한 단계 키우고 최대 폭은 `max-w-4xl`로 확장해
  4열 멤버십 지표와 actor·team·resource 정보를 읽기 쉽게 유지한다.
- Organization member access semantics와 API contract는 [organization component spec](../organization/component_spec.md)의 `ActorAccessDrawer`와 [organization API spec](../organization/api_spec.md)을 따른다.
- Summary 영역: actor name/email, global user active state, membership state, organization role, effective access, control block reason. 요약에서 membership state는 `활성`/`정지`, organization role은 `멤버`/`관리자`, global user state는 `활성`/`비활성`, effective access는 `허용`/`차단`으로 표시한다. Role set은 `member`/`manager` desired value별 control을 구분한다.
- Source 영역: team membership count와 paginated active/inactive membership, App creation source, direct/team resource source counts.
- Resource 영역: workflow/Knowledge Base/LLM credential filter, direct/team source filter, pagination. Team membership 목록도 별도 pagination을 사용한다.
- Catalog team/resource 선택은 exact `teamId`/`resourceId` 조회로 current page 밖 existing row를 확인한 뒤 confirm precondition을 구성한다. Exact 조회가 실패하면 absence로 간주하지 않고 추가/부여 action을 disabled 처리한다.
- Action 영역: suspend/reactivate, role set, team membership add/remove, direct permission grant/revoke, App creation grant/revoke.
- Active manager override target은 role 강등 전 resource/team/App action을 disabled 처리한다. Suspended target의 stored manager role은 override로 표시하지 않는다. Server 409가 최종 방어다.
- Suspended target은 stored source를 표시하되 role promotion과 신규 grant/restore control은 reactivation 전 disabled 처리한다. Manager-to-member 강등과 remove/revoke cleanup은 허용한다.
- Globally deactivated target은 cleanup-only로 표시하고 reactivate/promotion/add/grant control을 disabled 처리한다.

### ActorAccessConfirmDialog (FR-017)

- Target user, action 종류, resource/team, current/next state, effective impact를 표시한다.
- Optional reason textarea는 최대 500자이고 JSON body의 `reason`으로 전송한다.
- Client는 server와 같은 newline/code-point/control/bidi validation을 적용하되, durable redaction과 authorization source로 사용하지 않는다.
- Confirm 한 번에 access action 하나만 제출한다.
- Payload에는 profile user-active/membership id/state/role과 action별 source row precondition을 포함한다.
- 처리 중 confirm button을 disabled 처리한다. 성공 후 profile/source를 재조회하고, stale/conflict/not-found 응답은 기존 confirm을 닫아 stale payload 재제출을 막은 뒤 최신 상태를 재조회한다.

### PermissionRequestsTab (FR-014)

별도 `권한 신청` 메뉴를 두지 않고 기존 `권한` 탭의 resource 권한 관리 카드 아래에 렌더링한다. 기존 주소 `/dashboard/admin?tab=permission-requests`로 접근하면 `/dashboard/admin?tab=permissions`로 정규화해 새로고침과 기존 링크를 안전하게 유지한다.

- status 필터: 기본 `pending`, `approved`/`rejected` 전환 가능.
- 테이블 컬럼: 요청자(이름/이메일), 요청 권한(`app.create`의 사용자 친화 라벨 — "workflow 생성/배포"), 신청 사유, 신청일, 상태 배지. 처리된 건은 처리자/처리 시각 표시.
- pending 행에만 `승인`/`거절` 버튼을 인라인으로 둔다.
- 버튼 클릭 → `ConfirmDialog`: 요청자, 요청 권한, 신청 사유를 재표시하고 확정을 받는다. 되돌릴 수 없는 액션이므로 즉시 처리하지 않는다.
- 확정 시 `POST /admin/permission-requests/{id}/approve|reject` 호출. 성공하면 toast(기존 sonner)로 알리고 목록을 갱신한다.
- 데이터 원천: `GET /admin/permission-requests`.

같은 카드 하단에 `보유 권한` 섹션을 둔다 (FR-014 회수 확장).

- 테이블 컬럼: 보유자(이름/이메일), 부여자, 부여일. 행별 `회수` 버튼을 인라인으로 둔다.
- organization owner/manager는 row 없이 허용되므로 이 목록에 나타나지 않는다. 섹션 설명에 이 사실을 안내하고, 빈 목록은 "부여된 App 생성 권한이 없습니다" empty state로 표시한다.
- `회수` 클릭 → `ConfirmDialog`: 보유자와 권한 라벨(`app.create`의 사용자 친화 라벨)을 재표시하고 확정을 받는다. 되돌릴 수 없는 액션이므로 즉시 처리하지 않는다.
- 확정 시 `DELETE /admin/app-creation-permissions/{permission_id}` 호출. 성공하면 toast로 알리고 보유 목록을 갱신한다. 회수된 사용자는 재신청할 수 있으므로 신청 목록도 함께 갱신한다.
- `404` 응답(이미 회수됐거나 없는 row)은 "이미 회수된 권한입니다" toast 후 목록 갱신.
- 데이터 원천: `GET /admin/app-creation-permissions`.

### PermissionsTab

- `PermissionsTab`은 App Router의 route segment export 계약을 지키도록 `page.tsx`와 분리된 컴포넌트로 유지한다.
- 권한 부여 modal은 resource와 grantee를 checkbox로 다중 선택하고 선택 Cartesian product 최대 50건을 하나의 bulk grant로 제출한다. 검색 결과에 현재 resource 또는 grantee 선택값이 보이지 않으면 저장 action을 비활성화하고, 제출 시점에도 같은 조건을 다시 확인한다.

### KnowledgePermissionManagement (MBA-176)

- 기존 admin/settings permission UI를 재사용해 Knowledge Base별 team permission과 user direct permission을 관리한다.
- 목록 데이터 원천: `GET /permissions/knowledge-bases/{knowledge_base_id}`.
- Grant action: `PUT /permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}`, `PUT /permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}`.
- Revoke action: `DELETE /permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}`, `DELETE /permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}`.
- User direct grant selector는 `viewer`, `operator`, `builder`, `manager`만 제공한다. `none`은 회수 action으로 표현한다.
- UI는 active organization member라는 사실과 KB 사용 가능 여부를 구분해 표시한다. 조직 멤버십은 grant 대상 조건이지만 KB `use/read/manage` 권한 자체가 아니다.

### UsageTab (FR-012)

- 기간 필터: 기본 이번 달(KST), `startAt`/`endAt` 지정 가능.
- 테이블 row 기준: organization scope 안의 App primary workflow 전체. 기간 안에 사용량이 없는 workflow도 표시하고 호출 수, prompt/completion tokens, 비용은 0으로 보여준다.
- 테이블 컬럼: workflow 이름, 호출 수, prompt/completion tokens, 비용(USD 2자리), 예산. 비용 칸은 기존 총비용을 주 값으로 유지하고 그 아래에 `워크플로 실행`과 `Agent Builder` 비용을 표시한다. 별도 열을 늘리지 않는다. 총비용 내림차순 고정 정렬이며 같은 비용에서는 workflow 이름/id 순서로 안정적으로 보인다.
- 예산 컬럼은 활성 예산이 있으면 예산 금액(USD 2자리), 사용률(%), 상태 배지(`BudgetStatusBadge`)를 표시한다. `budget` null이면 "미설정"을 표시한다.
- 모든 행에 `예산 설정` 버튼을 제공하고, 클릭 시 `BudgetEditModal`을 열어 `GET/PUT /admin/workflow-budgets/{workflow_id}`로 조회/저장한다. 저장 성공 시 비용 목록과 요약 카드를 다시 조회한다.
- 행에 해당 workflow로 이동하는 링크/버튼을 둔다 — 비용 최적화 실행은 workflow 문맥의 [cost-optimizer](../cost-optimizer/component_spec.md) 범위이며 이 탭은 진입만 제공한다.
- 데이터 원천: `GET /admin/usage/workflows`.

## States

- 각 탭 공통: 로딩(스켈레톤 또는 스피너), 빈 목록(안내 문구 포함 empty state), 오류(재시도 버튼).
- 비용 탭의 빈 목록은 organization scope 안에 표시할 App primary workflow가 없을 때만 사용한다. 기간 안에 usage가 없는 workflow는 빈 목록이 아니라 사용량 0 row로 표시한다.
- 권한 탭의 권한 신청 처리 중: 해당 행 버튼 비활성화(중복 클릭 방지). 409 응답(이미 처리된 신청)은 "이미 처리된 신청입니다" toast 후 목록 갱신.
- 권한 회수 처리 중: 확인 다이얼로그의 버튼을 비활성화한다(중복 클릭 방지, modal이 행 버튼 접근을 막는다). 404 응답(이미 회수된 권한)은 "이미 회수된 권한입니다" toast 후 목록 갱신.
- Actor access profile 404: current organization에서 관리할 수 없는 historical actor 안내 후 audit detail은 유지한다.
- Actor access action 409: last manager, manager override, target user inactive, stale member state에 맞는 안전한 안내를 표시하고 profile을 재조회한다.
- Actor access action audit/transaction failure: 성공 상태로 낙관 반영하지 않고 오류와 retry를 제공한다.
- 요약 카드의 `budget` null 상태: "예산 미설정" 표시 (오류 아님).
- (후순위) auditor 전용 사용자: 감사 로그 탭 단독 노출 상태. auditor 전용 노출 제어와 함께 복원한다.
- 403 응답: 접근 권한 안내 문구 (프론트 노출 제어를 우회한 접근 대비).

## Interactions

1. 탭 전환: 기존 admin 페이지 탭 패턴을 따른다. 탭 상태는 페이지 내 state로 유지한다.
2. audit 검색: 필터 변경 → 조회 버튼 또는 디바운스 적용 → cursor history 초기화 → 첫 페이지부터 재조회.
3. audit 행 클릭 → 드로어 열림. ESC/바깥 클릭/닫기 버튼으로 닫힘.
4. audit user actor 클릭 → ActorAccessDrawer → 항목 선택 → ActorAccessConfirmDialog → single access action → profile/resource/audit 재조회.
5. 권한 탭의 권한 신청 카드에서 `승인` 클릭 → ConfirmDialog → 확정 → API 호출 → 성공 toast → 목록 갱신. 거절도 동일 흐름.
6. 비용 행의 workflow 링크 클릭 → 해당 workflow 화면으로 이동.
7. 요약 카드는 페이지 진입 시 로드하고 탭 전환과 무관하게 유지한다.

## Accessibility

- 드로어와 ConfirmDialog는 포커스 트랩, ESC 닫기, 적절한 `role`(`dialog`)과 `aria-label`을 갖는다.
- Actor button은 keyboard-focusable element를 사용하고 actor drawer를 닫으면 해당 button으로 focus를 복원한다.
- 테이블은 `<th>` 헤더와 캡션을 갖고, 정렬 기준(비용 내림차순)을 시각적으로 표시한다.
- status/상태 배지는 색상 외에 텍스트를 병기한다 (색맹 대응).
- 승인/거절 버튼은 처리 중 `disabled`와 로딩 표시를 제공한다.
- 시간 표시는 `<time datetime>` 속성에 ISO 값을 유지한다 (표시는 사용자 로컬).
