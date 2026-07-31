# Security Alert Component Spec

Status: Draft

이 문서는 [requirements.md](requirements.md)와 [api_spec.md](api_spec.md)의 Security Alert 관리자 UI, Sidebar 알림, SSE 재조회 흐름을 정의한다. 탐지 정책과 lifecycle은 [ADR-0028](../../decisions/ADR-0028-security-alert-detection-and-lifecycle.md)을 따른다.

## Product Surface

Security Alert는 두 진입점을 제공한다.

1. Sidebar의 기존 알림 overlay에서 open alert 요약을 확인한다.
2. Admin Dashboard의 `보안 알림` 탭에서 검색·상세 확인·상태 변경을 수행한다.

Security Alert UI는 실제 침해를 확정하는 화면이 아니라 관리자가 확인해야 할 위험 신호를 보여주는 화면이다. 자동 사용자 정지나 자동 권한 회수는 제공하지 않는다.

## Admin Dashboard Navigation

### Tab Placement

Admin Dashboard 탭 순서는 다음과 같다.

```text
멤버 → 팀 → 권한 → 권한 신청 → 비용 → LLM Credentials
→ 지식 기반 → 보안 알림 → 감사 로그 → 조직 설정
```

- 표시 이름: `보안 알림`
- 내부 key: `security-alerts`
- 현재 organization owner/manager에게만 탭을 표시한다.
- 일반 member가 URL로 직접 접근해도 API가 최종 권한을 검사한다.

### URL State

Security Alert 진입 상태는 URL query parameter와 동기화한다.

```text
/dashboard/admin?tab=security-alerts
/dashboard/admin?tab=security-alerts&alertId=<uuid>
```

- `tab=security-alerts`는 보안 알림 탭을 연다.
- 유효한 `alertId`가 있으면 목록을 유지한 채 해당 상세 drawer를 연다.
- 상세 drawer를 닫으면 `alertId`만 URL에서 제거하고 `tab=security-alerts`는 유지한다.
- 다른 Admin tab을 선택하면 `alertId`를 제거하고 선택한 tab을 URL에 반영한다.
- 지원하지 않는 `tab` 값은 현재 기본 tab인 `members`로 정규화한다.
- UUID 형식이 아닌 `alertId`는 상세 API를 호출하지 않고 URL에서 제거하며 safe 안내를 표시한다.
- 상세 조회가 404면 alert 존재나 다른 organization 여부를 구분하지 않고 drawer를 닫은 뒤 `알림을 찾을 수 없습니다.`를 표시한다.
- 새로고침, 브라우저 뒤로/앞으로 이동, 공유 링크에서도 URL과 열린 tab/drawer 상태가 일치해야 한다.

## Component Structure

```text
AdminConsolePage
└── SecurityAlertsTab
    ├── SecurityAlertFilters
    ├── SecurityAlertTable
    ├── AdminPagination
    ├── SecurityAlertDetailDrawer
    │   ├── SecurityAlertEvidenceList
    │   ├── Acknowledge action
    │   ├── ResolveAlertDialog
    │   └── User access management action
    └── ActorAccessDrawer (detail drawer와 동시에 열지 않음)

Sidebar
└── NotificationOverlay
    ├── SecurityAlertNotificationSection
    └── OrganizationInvitationSection
```

신규 API client와 type은 기존 admin feature 구조를 따른다.

```text
apps/client/app/features/admin/
├── api/adminApi.ts
├── types/SecurityAlert.ts
└── components/
    ├── SecurityAlertsTab.tsx
    ├── SecurityAlertDetailDrawer.tsx
    └── ResolveAlertDialog.tsx
```

실제 구현에서 파일을 더 나눌 수 있지만 API 호출, 상태 전이, drawer focus 책임이 한 컴포넌트에 과도하게 섞이면 안 된다.

## Security Alerts Tab

### Header

탭 상단은 다음 정보를 제공한다.

- 제목: `보안 알림`
- 설명: `반복된 권한 거부와 정책 차단에서 탐지된 위험 신호입니다.`
- 이 화면이 실제 침해 확정을 의미하지 않는다는 짧은 안내

자동 차단, 규칙 편집, 외부 알림 설정 control은 표시하지 않는다.

### Filters

| Control | API parameter | Options |
| --- | --- | --- |
| 심각도 | `severity` | 전체, 보통(`medium`), 높음(`high`) |
| 상태 | `status` | 전체, 미확인(`open`), 확인됨(`acknowledged`), 해결됨(`resolved`) |
| 탐지 규칙 | `ruleId` | 전체와 지원하는 3개 rule |
| 사용자 | `actorId` | 현재 organization member safe projection |
| 시작 시각 | `startAt` | datetime |
| 종료 시각 | `endAt` | datetime |

- 초기 상태는 모든 필터가 `전체` 또는 비어 있는 상태다.
- `조회`를 눌렀을 때 filter를 적용하고 page를 1로 초기화한다.
- `초기화`는 모든 filter를 지우고 page 1의 전체 목록을 다시 조회한다.
- 종료 시각이 시작 시각보다 늦지 않으면 API 호출 전에 안내하고 조회하지 않는다.
- Filter label과 option은 사용자용 문구를 사용하고 API에는 canonical enum을 보낸다.
- Filter 변경 중 draft와 현재 적용된 filter를 분리해 pagination이 미적용 draft 값을 사용하지 않게 한다.

### Rule Labels

| Rule ID | UI label |
| --- | --- |
| `repeated_permission_denied` | 반복된 권한 거부 |
| `multi_resource_permission_probe` | 여러 리소스 접근 시도 |
| `repeated_policy_block` | 반복된 정책 차단 |

Unknown rule ID는 임의 번역하지 않고 safe fallback `알 수 없는 탐지 규칙`으로 표시한다.

### Severity And Status Labels

| Value | Label | Visual meaning |
| --- | --- | --- |
| `medium` | 보통 | 주의가 필요한 신호 |
| `high` | 높음 | 우선 확인할 신호 |
| `open` | 미확인 | 아직 관리자가 확인하지 않음 |
| `acknowledged` | 확인됨 | 관리자가 확인하고 조사 중 |
| `resolved` | 해결됨 | 대응 또는 오탐 처리가 끝남 |

Color만으로 severity나 status를 구분하지 않고 항상 text label을 함께 표시한다.

### Table

목록은 다음 column을 표시한다.

| Column | Content |
| --- | --- |
| 심각도 | severity label |
| 탐지 유형 | rule label |
| 사용자 | safe display name, 없으면 삭제된 사용자/opaque ID |
| 상태 | status label |
| 발생 횟수 | `occurrence_count` |
| 최초 탐지 | `first_detected_at` |
| 최근 탐지 | `last_detected_at` |

- 기본 정렬은 API가 제공하는 `last_detected_at DESC`, `id DESC`를 따른다.
- Client-only 재정렬은 제공하지 않는다.
- Row click과 명시적 `상세 보기` control은 같은 detail drawer를 연다.
- Row는 keyboard로 접근 가능해야 하며 click 가능한 `tr`에만 의존하지 않는다.
- `policy_reason` 원문 코드는 목록에서 기본 표시하지 않고 상세에서 safe label과 함께 표시한다.
- Email, raw target, raw metadata는 표시하지 않는다.

### Pagination

- 기존 `AdminPagination`을 재사용한다.
- Page 변경 시 적용된 filter를 유지한다.
- 현재 page가 mutation 또는 재조회 뒤 비게 되면 유효한 이전 page로 이동한다.

## Loading, Empty, Error States

### Initial Loading

- 목록 영역에 `보안 알림을 불러오는 중...`을 표시한다.
- 이전 organization의 목록을 새 organization loading 중에 표시하지 않는다.
- 같은 organization·filter·page의 background refresh는 기존 목록을 지우거나 initial loading 화면으로 교체하지 않는다. 응답 성공 시 목록과 발생 횟수를 한 번에 갱신하고, 재시도 가능한 실패 시에도 기존 목록을 유지한다.

### Empty

- Filter가 없고 alert가 없으면 `탐지된 보안 알림이 없습니다.`를 표시한다.
- Filter 결과가 없으면 `조건에 맞는 보안 알림이 없습니다.`와 `초기화` action을 표시한다.

### Error

- 재시도 가능한 오류는 safe message와 `다시 시도`를 표시한다.
- `403`은 관리 권한이 없음을 표시하고 목록과 cached alert를 제거한다.
- `404` detail 오류는 alert 존재 여부를 설명하지 않는다.
- `409 stale_state` mutation 오류는 최신 detail을 다시 조회한 뒤 `다른 관리자가 상태를 변경했습니다.`를 표시한다. 최신 detail 재조회가 실패하면 stale detail과 상태 변경 action을 제거하고 safe 오류를 표시한다.
- API raw error, stack, response body를 그대로 출력하지 않는다.

## Security Alert Detail Drawer

### Open And Close

- Drawer를 열면 URL에 `alertId`를 반영하고 detail과 evidence 첫 page를 조회한다.
- Drawer close 시 `alertId`를 제거하고 열었던 row/detail trigger로 focus를 복원한다.
- Escape와 close button으로 닫을 수 있다.
- Drawer가 열린 동안 background focus 이동을 막는다.
- Organization이 바뀌거나 관리 권한이 사라지면 즉시 drawer를 닫고 cached detail을 제거한다.
- 본문 typography는 기존 `text-xs → text-sm`, `text-sm → text-base`,
  `text-base → text-lg`, `text-lg → text-xl`로 한 단계씩 키우고 최대 폭은
  `max-w-4xl`로 확장한다. 연결된 감사 기록의 Audit detail drawer도 같은 typography를
  사용하되 최대 폭은 `max-w-xl`로 유지해 actor·권한 문구의 불필요한 줄바꿈을 줄인다.

### Summary Content

상단에 다음을 표시한다.

- 위험 신호 안내
- Severity와 status
- Rule label과 rule version
- Safe actor projection
- Policy reason safe label 또는 `-`
- Occurrence count
- 최초·최근 탐지 시각
- Acknowledge 요약
- Resolution 요약

Detection key, raw target 목록, raw audit metadata는 표시하지 않는다.

### Policy Reason Labels

| Canonical reason | UI label |
| --- | --- |
| `access_management.self_control_forbidden` | 본인 접근 상태 변경 제한 |
| `access_management.last_active_manager` | 마지막 관리자 보호 |
| `access_management.manager_override_active` | 관리자 권한 보호 |
| `access_management.member_state_not_manageable` | 현재 멤버 상태에서 허용되지 않은 작업 |
| `access_management.target_user_inactive` | 비활성 사용자 대상 작업 제한 |
| `access_management.stale_state` | 최신 상태와 다른 요청 |
| `rag.pii_evidence_detected` | 개인정보 포함 근거 감지 |
| `knowledge.sensitive_content_detected` (Target MBA-362) | 지식 문서 민감정보 감지 |

Unknown reason은 원문을 사용자 문장으로 만들지 않고 `알 수 없는 정책 사유`로 표시한다.

### Related Audit Evidence

- Detail drawer 안에서 `/audit-logs`를 별도 pagination으로 조회한다.
- Audit occurred time과 함께 `현재 조직에 대한 접근이 거부되었습니다.` 같은 사용자용 설명을 먼저 표시한다.
- Safe 권한 거부 정보가 있으면 `보안 알림 목록 조회를 시도했지만 조직 관리자 권한이 필요해 거부되었습니다.`처럼 시도한 작업과 원인을 표시한다. 기존 기록처럼 정보가 없으면 일반 설명으로 대체한다.
- Canonical action과 safe target ID는 조사 가능하도록 보조 정보로 유지하고, target type과 status는 사용자용 라벨로 표시한다.
- Raw metadata, before/after를 표시하지 않는다.
- Audit row의 `상세 보기`는 기존 Audit detail 경로를 사용하되 기존 audit 권한과 allowlist를 다시 적용한다.
- Audit detail drawer는 Security Alert detail보다 높은 layer에 표시하고, 닫으면 Security Alert detail과 선택했던 row focus를 복원한다.
- Evidence loading/error는 alert detail 전체 loading/error와 분리한다.
- Evidence가 없으면 `연결된 감사 기록이 없습니다.`를 표시하되 alert 자체를 invalid로 단정하지 않는다.
- Security Alert 관리 권한 거부의 `requested_operation`은 고정 operation 타입과 라벨 매핑으로 표시한다. Operation이 없는 이전 기록은 `보안 알림 관련 작업`을 fallback으로 사용한다.

## Lifecycle Actions

### Action Availability

| Current status | Actions |
| --- | --- |
| `open` | 확인, 해결 |
| `acknowledged` | 미확인으로 되돌리기, 해결 |
| `resolved` | 상태 변경 없음 |

Resolved alert는 reopen하지 않는다. 재발은 새 threshold를 충족한 새 alert로 표시한다.

### Acknowledge

- `확인` action은 current `version`을 `expected_version`으로 전송한다.
- 성공하면 detail과 목록 item을 응답 값으로 갱신하고 Sidebar summary를 재조회한다.
- 요청 중 같은 action을 중복 제출할 수 없게 한다.
- Acknowledge, reopen, resolve가 `403`을 반환하면 stale detail과 해결 dialog를 비우고 drawer를 닫아 URL의 `alertId`를 제거한다. 같은 action button을 남겨 반복 요청하게 해서는 안 된다.

### Reopen

- UI label은 `미확인으로 되돌리기`를 사용한다.
- API action은 `reopen`이며 acknowledged alert에만 표시한다.
- 성공하면 acknowledged 현재 요약이 사라지고 status가 open으로 바뀐다.
- Sidebar open badge와 summary를 재조회한다.

### Resolve Dialog

Dialog는 다음 field를 제공한다.

| Field | Control | Required |
| --- | --- | --- |
| 처리 결과 | select/radio: 대응 완료, 오탐, 위험 수용 | yes |
| 처리 사유 | textarea | yes |

Mapping:

| UI label | API value |
| --- | --- |
| 대응 완료 | `mitigated` |
| 오탐 | `false_positive` |
| 위험 수용 | `accepted_risk` |

- Reason은 query string이나 URL에 넣지 않는다.
- Blank, 500 Unicode code point 초과, 허용되지 않은 control character는 client에서 먼저 안내한다.
- Server validation이 최종 기준이며 client validation만 신뢰하지 않는다.
- Dialog는 현재 alert version을 `expected_version`으로 보낸다.
- 성공 후 dialog를 닫고 detail/list/summary를 응답과 재조회 결과로 갱신한다.
- 실패 시 입력 내용을 유지하되 raw server error를 표시하지 않는다.
- Submit 중 중복 제출을 막고 close/cancel 동작을 명확히 제공한다.

## User Access Management

Alert detail은 자동 차단 또는 즉시 차단 button을 제공하지 않는다.

```text
Security Alert detail
→ 사용자 접근 관리
→ 기존 ActorAccessDrawer
→ 관리자가 현재 상태와 권한을 확인
→ 필요한 action을 별도 confirm으로 수행
```

- `사용자 접근 관리`는 safe actor가 현재 organization member로 해석되고 manager가 관리 가능한 경우에만 활성화한다.
- Actor가 deleted/removed이거나 현재 profile을 조회할 수 없으면 disabled 상태와 safe 이유를 표시한다.
- Button을 누르면 현재 `alertId` URL 상태를 유지한 채 Security Alert detail drawer를 잠시 숨기고 기존 `ActorAccessDrawer`를 연다.
- 두 drawer를 동시에 표시하지 않는다.
- ActorAccessDrawer 상단에는 `보안 알림 상세로 돌아가기` action을 표시한다. 이 action 또는 Escape로 닫으면 유지한 `alertId`의 Security Alert detail을 다시 열고 최신 상세를 재조회한다.
- 기존 actor access API, stale precondition, confirm, reason, policy block, canonical audit 계약을 그대로 재사용한다.
- 가능한 수동 조치는 organization membership 정지, role 변경, team 제거, direct permission 회수, App 생성 권한 회수다.
- 자기 자신, 마지막 manager, manager override 같은 기존 보호 정책을 우회하지 않는다.
- 수동 조치 성공 후 Security Alert를 자동 resolve하지 않는다. 돌아온 alert detail에서 관리자가 별도로 resolve해야 한다.

## Sidebar Notification Overlay

### Entry And Badge

- 기존 Sidebar 사용자 menu의 `알림` 진입점을 유지한다.
- Organization owner/manager에게만 Security Alert open badge를 추가한다.
- Badge count는 summary API의 `open_count`이며 acknowledged/resolved를 포함하지 않는다.
- 일반 member는 Security Alert summary를 요청하거나 badge를 보지 않지만 기존 organization invitation 알림은 계속 사용할 수 있다.

### Overlay Sections

기존 `NotificationOverlay`를 다음 두 source section으로 확장한다.

```text
알림
├── 보안 알림
│   ├── open count
│   └── 최근 open alert 최대 5개
└── 조직 초대
    └── 기존 수락/거절 목록
```

- `보안 알림` section은 owner/manager에게만 표시한다.
- `조직 초대` section은 기존 invitation API와 동작을 유지한다.
- 두 section은 loading/error/empty 상태를 독립적으로 표시한다.
- 한 source가 실패해도 다른 source의 정상 내용을 숨기지 않는다.
- Security Alert item은 severity, rule label, safe actor, occurrence count, recent time을 표시한다.
- Alert item click은 overlay를 닫고 `/dashboard/admin?tab=security-alerts&alertId=<uuid>`로 이동한다.
- `모두 보기`는 `/dashboard/admin?tab=security-alerts`로 이동한다.
- Invitation item에는 기존 수락/거절 action만 표시한다.
- Security Alert item에는 overlay 안에서 acknowledge/resolve action을 제공하지 않는다.

## SSE And Refresh

- 기존 `/api/v1/notifications/stream`과 `notifications.changed` event를 재사용한다.
- Event payload를 alert source of truth로 사용하지 않는다.
- Worker는 Alert 생성 또는 새 evidence에 의한 occurrence 갱신이 실제로 commit된 organization만 event 발행 대상으로 추가한다. Threshold 전 event처럼 aggregation 결과가 없으면 발행하지 않는다.
- 수신자 집합은 현재 manager 권한 판정과 일치해야 하며 active manager membership과 membership 없는 유효한 organization `created_by`/`managed_by`를 포함한다. Suspended, removed, deactivated owner는 제외하고 중복 user는 한 번만 발행한다.
- Event 수신 시 invitation 목록은 항상 재조회하고, 현재 organization manager이면 Security Alert summary도 재조회한다. 별도 client refresh event로 열려 있는 Security Alert 목록/detail도 다시 조회한다.
- Security Alert tab 또는 detail 재조회는 적용 중인 filter, 현재 page, evidence page를 유지한다.
- 같은 scope의 background refresh 중에는 현재 목록, detail과 evidence를 유지한다. 새 응답이 성공하면 화면 데이터를 한 번에 교체하고, 재시도 가능한 실패에는 기존 데이터를 유지한다. Organization·filter·page·alert scope 변경이나 `403`에서는 이전 데이터를 즉시 제거한다.
- 최초 summary snapshot은 toast를 만들지 않는다. 이후 `notifications.changed` 재조회 결과에서 새 alert가 생기거나 같은 alert의 `occurrence_count`가 증가했을 때만 빨간색 경고 아이콘과 `새 보안 알림이 있습니다.`라는 일반 문구를 표시한다.
- 같은 alert의 toast에는 60초 client cooldown을 적용한다. 여러 alert가 한 번에 바뀌어도 한 번의 summary refresh에서는 toast 하나만 표시한다.
- SSE `open` event는 invitation과 권한에 맞는 Security Alert summary/list/detail을 재조회해 초기 연결과 reconnect 누락을 복구하되 toast는 만들지 않는다.
- Active organization 전환 시 이전 organization summary, 목록, detail을 즉시 제거하고 새 scope를 조회한다.
- Active organization 전환 시 이전 summary snapshot과 toast cooldown도 제거한다.
- Membership/manager 권한 회수로 summary가 403이면 manager UI를 숨기고 Security Alert badge, 목록, detail cache와 선택된 detail을 제거한다. Invitation source는 계속 독립적으로 동작한다.

## Accessibility

- Tab, filter, table action, drawer, dialog는 keyboard로 사용할 수 있어야 한다.
- Drawer와 dialog는 적절한 dialog role, accessible name, focus trap, Escape close를 제공한다.
- Drawer를 닫으면 가능한 경우 열었던 trigger로 focus를 복원한다.
- 두 drawer를 전환할 때 닫힌 drawer 내부 element로 focus를 복원하지 않는다.
- Severity/status는 color뿐 아니라 text로 전달한다.
- Loading 상태는 screen reader가 인식할 수 있게 표시한다.
- Mutation 성공·실패와 stale refresh 결과는 접근 가능한 live feedback으로 전달한다.

## Responsive Behavior

- Tab navigation은 기존처럼 가로 scroll을 허용한다.
- 좁은 화면에서 filter는 세로 배치하고 table은 안전한 가로 scroll 또는 compact row layout을 사용한다.
- Drawer와 dialog action은 viewport 밖으로 밀리지 않아야 한다.
- 긴 safe reason과 actor 표시값은 layout을 깨지 않고 wrap 또는 truncate하며 전체 의미를 접근 가능하게 제공한다.

## Client State And Caching

- Security Alert query cache key는 최소 organization ID, filter, page를 포함한다.
- Detail cache는 organization ID와 alert ID를 함께 사용한다.
- Organization 전환 시 이전 scope cache를 화면에 재사용하지 않는다.
- Mutation 성공 response를 우선 반영하고 summary/list를 재조회해 최종 정합성을 맞춘다.
- `409 stale_state`에서는 optimistic 값을 유지하지 않고 server detail을 재조회한다. 이 재조회는 기존 데이터를 유지하는 background refresh와 구분하며, 실패 시 stale version으로 추가 mutation을 허용하지 않는다.
- Raw alert metadata나 resolution reason을 local storage에 저장하지 않는다.

## Component Test Expectations

- URL query로 Security Alert tab과 detail drawer가 복원된다.
- Invalid/404 alert ID가 다른 organization 존재 여부를 노출하지 않는다.
- Filter draft와 applied state, pagination이 분리된다.
- Open/acknowledged/resolved별 action이 정확히 표시된다.
- Resolve validation과 `409 stale_state` refresh가 동작한다.
- ActorAccessDrawer 전환 시 두 drawer가 겹치지 않고 focus가 안전하다.
- Owner/manager에게만 Security Alert tab, badge, overlay section이 보인다.
- Security Alert source 오류가 invitation section을 숨기지 않는다.
- `notifications.changed` 수신과 reconnect 후 invitation, 권한에 맞는 summary, 열린 list/detail을 재조회한다.
- 최초/reconnect 조회는 toast를 만들지 않고, 새 alert 또는 occurrence 증가만 일반 문구로 알리며 같은 alert는 60초 안에 한 번만 알린다.
- Active organization 전환과 권한 회수 시 이전 scope 데이터가 제거된다.
- 기존 invitation 수락/거절과 Audit/ActorAccess 흐름이 회귀하지 않는다.
