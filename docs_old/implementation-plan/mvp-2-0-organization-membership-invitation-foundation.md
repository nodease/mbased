# MVP 2-0 Organization Membership / Invitation Foundation 계획서

Status: Draft
Authority: Implementation Plan
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)
Original Basis: origin/dev @ cde421f2cbd98d0ede4e00cac2150ece36e413bd

편입 메모: 이 문서는 첨부 계획서를 `docs/implementation-plan/`의 active 구현 계획으로 편입한 것이다. 원본 계획서의 검증 기준은 `Original Basis`에 보존했다. MBA-66에서 `organization_memberships` DB/model/migration foundation이 구현됐고, MBA-67에서 permission helper/API 일부가 organization membership 기준으로 전환됐다. MBA-71에서는 active organization과 manager/member 화면 분기가 일부 반영됐다. Organization member/invitation BE API는 구현됐고, full membership 관리 UI는 아직 후속 범위다.

## 1. 목적

이 문서는 MVP 2의 Governance/RAG/Audit 개발 전에 선행해야 했던 `MVP 2-0` 작업을 정의한 계획서다. Dev 기준 DB/model/migration/backfill과 permission helper 전환은 완료됐고, 현재는 완료된 BE foundation과 남은 full membership UI 범위를 구분해 읽는다.

MVP 1이 완료되었다고 가정하면 현재 RBAC foundation은 다음 구조를 가진다.

```text
organization
  -> teams
    -> team_memberships
      -> users

team_*_permissions
user_*_permissions
```

이 구조는 resource permission 계산에는 동작하지만, 제품 관점에서는 다음 문제가 있다.

1. 사용자를 organization에 초대한다는 1차 개념이 없다.
2. 사용자가 organization에 속해 있는지 확인하려면 team membership을 거쳐야 한다.
3. team에 넣는 행위가 organization 초대처럼 사용된다.
4. 특정 user에게 direct permission을 부여하려면 그 user가 어떤 team에든 먼저 들어가야 한다.
5. 다중 organization 사용자가 늘어날수록 active organization 판단이 비직관적이다.
6. MVP 2의 knowledge base/document permission은 organization member 기반으로 부여되어야 하는데, 현재 구조는 team membership에 과도하게 의존한다.

MVP 2-0 계획은 `organization_memberships`를 추가하는 방향으로 수립됐고, dev 기준 DB/model/migration/backfill과 permission helper 전환은 완료됐다. 목표 상태는 다음과 같다.

```text
organization
  -> organization_memberships
    -> users

organization
  -> teams
    -> team_memberships
      -> users

team_*_permissions
user_*_permissions
```

핵심 변화는 `organization_memberships`가 "사용자가 organization에 속한다"는 source of truth가 되고, `team_memberships`는 "organization 안에서 user가 특정 team에 속한다"는 하위 배정 정보로 축소되는 것이다.

## 2. 배경

### 2.1 현재 문서 기준

기존 MVP 1 문서는 user subject를 team membership과 user direct grant로 설명한다.

```text
team: 기본 권한 subject. user는 team membership을 통해 권한을 얻는다.
user: 예외적 추가 권한 subject. resource별 user_*_permissions로 additive allow만 부여한다.
```

원 계획 작성 당시에는 일부 문서와 구현 경로가 `team_memberships`를 organization 소속의 간접 원천처럼 해석했다. 이 전제는 현재 공식 데이터 모델 기준에서는 폐기된 전제다.

```text
폐기된 과거 전제: 사용자 소속 = team_memberships
```

현재 기준은 다음처럼 분리한다.

```text
사용자 organization 소속 = organization_memberships
사용자 team 소속 = team_memberships
```

### 2.2 현재 코드 기준

원 계획 작성 당시에는 active organization과 user direct permission의 전제 조건이 `team_memberships`에 과도하게 의존했다. 최신 dev 기준으로 BE foundation과 organization member/invitation API 전환은 완료됐고, 남은 작업은 full membership 관리 UI, team/direct permission picker 필터 반영, legacy fallback 축소 정책이다.

| 파일 | 최신 dev 상태 | 남은 방향 |
| --- | --- | --- |
| `apps/gateway/services/organization_context.py` | active `organization_memberships` 기반 primary organization/helper 사용 | legacy fallback 제거 시점은 별도 결정 필요 |
| `apps/gateway/services/team_service.py` | team member 추가와 user direct permission grant 대상에 active organization membership을 요구 | organization invite/accept flow가 생기면 대상 선택 UX와 연결 |
| `apps/shared/services/permissions.py` | organization scope/manager 판정은 `organization_memberships` 우선, team permission 계산은 `team_memberships` join 유지 | knowledge/audit user direct permission 추가 시 같은 전제 조건 적용 |
| `apps/shared/db/models/team.py` | `TeamMembership`은 organization 안의 team 배정 역할로 유지 | organization 소속 자체를 대신하지 않도록 유지 |
| `docs/data-model/physical-data-model.md` | `organization_memberships`를 현재 organization 소속 기준으로 반영 | 후속 schema extension 시 planned table 상태 갱신 |
| `docs/api/organization-rbac.md` | user directory, team, permission API의 membership 전제 조건과 organization member/invitation BE API를 반영 | full membership UI와 permission picker 연동 시 API 사용 흐름 갱신 |

## 3. 목표

아래 목록은 원 계획의 전체 목표다. 현재 PR 기준 BE foundation은 `organization_memberships` DB/model/migration/backfill, permission helper 전환, member/invitation API, accept flow, remove cleanup/audit 확장까지 구현됐다. Full membership 관리 UI는 후속 범위다.

1. organization manager가 기존 user를 organization에 초대할 수 있다.
2. 초대받은 user는 organization membership 상태를 가진다.
3. active membership이 있는 user만 해당 organization의 resource permission subject가 될 수 있다.
4. team membership은 organization membership이 active인 user에게만 부여할 수 있다.
5. team에 속하지 않아도 active organization member라면 user direct permission을 받을 수 있다.
6. active organization 목록은 team membership이 아니라 organization membership에서 조회한다.
7. 기존 MVP 1 데이터는 migration 후 organization membership을 잃지 않는다.
8. MVP 2의 knowledge base/document permission은 organization membership을 전제로 확장할 수 있다.

## 3.1 MVP 1 변경 최소화 원칙

이 작업은 MVP 1 RBAC foundation을 다시 설계하는 작업이 아니다. MVP 1에서 이미 동작한다고 가정하는 workflow/LLM credential 권한, audit, LLM trace, UI 흐름은 가능한 한 그대로 둔다.

반드시 지킬 원칙:

| 원칙 | 설명 |
| --- | --- |
| 기존 table 유지 | `organization`, `teams`, `team_memberships`, `team_*_permissions`, `user_workflow_permissions`, `user_llm_permissions`를 제거하거나 rename하지 않는다. |
| 기존 endpoint 유지 | MVP 1에서 구현된 team/resource permission endpoint path와 request/response shape를 깨지 않는다. |
| additive 변경 | `organization_memberships`는 새 전제 계층으로 추가한다. 기존 team permission 계산 방식은 유지한다. |
| migration으로 호환 | 기존 team membership, organization creator, organization manager를 모두 backfill하여 기존 사용자가 organization을 잃지 않게 한다. |
| active organization 계약 유지 | MVP 2-0만으로 header/session 기반 active organization 방식을 강제 도입하지 않는다. 기존 fallback을 membership 기반으로 바꾸되 API 계약 변경은 별도 계약 결정 후 active API/architecture 문서에 반영한다. |
| UI 재배치 최소화 | MVP 1 settings/RBAC UI는 유지하고 Members 탭과 picker 필터를 추가한다. 큰 navigation 개편은 하지 않는다. |
| 권한 의미 유지 | resource `auth_state`의 `viewer/operator/builder/manager` 의미는 유지한다. organization membership의 `member/manager`와 섞지 않는다. |

MVP 2-0에서 실제로 바꾸는 것은 "user가 organization에 속해 있는지 확인하는 기준"이다. 기존 "team membership이 있어야 user direct permission을 받을 수 있다"는 제약만 "active organization membership이 있어야 user direct permission을 받을 수 있다"로 완화한다.

## 4. 하지 않을 것

MVP 2-0에서는 범위를 의도적으로 제한한다.

| 제외 항목 | 제외 사유 |
| --- | --- |
| 이메일만 존재하는 미가입 사용자 초대 | invitation token, signup completion, 만료 정책이 추가되어 범위가 커진다. MVP 2-0은 기존 user 초대만 지원한다. |
| SSO/OIDC 기반 자동 organization join | MVP 2 범위가 아니다. |
| organization role catalog | `Admin`, `Builder`, `Viewer` 같은 role table을 만들지 않는다. |
| resource permission 통합 table | 기존 `team_*_permissions`, `user_*_permissions`를 유지한다. |
| node-level permission | MVP 범위 밖이다. |
| organization billing/seat limit | 운영/과금 기능이므로 후순위다. |
| 복잡한 approval workflow | 초대/수락/제거의 최소 상태만 둔다. |
| MVP 1 endpoint breaking change | 기존 team/resource permission API는 유지한다. |
| active organization header/session 강제 전환 | organization membership 기준으로 준비만 하고, 전달 방식 변경은 별도 결정 전에는 하지 않는다. |

## 5. 용어

| 용어 | 의미 |
| --- | --- |
| Organization membership | user가 organization에 소속되어 있다는 1차 관계 |
| Team membership | organization 안에서 user가 특정 team에 배정되어 있다는 2차 관계 |
| Invitation | organization manager가 user에게 organization join을 요청한 상태 |
| Active member | 초대를 수락했거나 migration/bootstrap으로 활성화된 organization member |
| Organization manager | organization member 중 organization 관리 권한을 가진 user |
| Resource permission | workflow, LLM credential, knowledge base 같은 resource에 대한 read/write/execute/use/manage 권한 |

## 6. 핵심 설계 결정

### 6.1 `users.organization_id`를 만들지 않는다

user는 여러 organization에 속할 수 있어야 한다.

```text
users 1:N organization_memberships N:1 organization
```

`users.organization_id`를 만들면 다중 organization이 어려워지고, active organization switcher와 초대 흐름이 비자연적이 된다.

### 6.2 `team_memberships`를 제거하지 않는다

team은 permission을 묶는 subject이므로 계속 필요하다.

다만 역할을 바꾼다.

| 이전 의미 | 변경 후 의미 |
| --- | --- |
| user가 organization에 속함을 간접 표현 | user가 organization 안의 특정 team에 배정됨 |
| organization 목록 조회 source | team 배정 조회 source |
| user direct permission 부여 전제 | team permission 계산에만 사용 |

### 6.3 organization membership만으로 resource 접근을 허용하지 않는다

active organization member라는 사실은 resource 접근의 필요조건이지 충분조건이 아니다.

```text
resource access =
  active organization membership
  + organization manager override 또는 resource permission
```

즉 일반 member는 organization에 속해 있어도 workflow, LLM credential, knowledge base 접근 권한을 자동으로 얻지 않는다.

### 6.4 organization manager 권한은 membership row로 표현하되 legacy fallback을 유지한다

기존 `organization.created_by`, `organization.managed_by`는 legacy 호환과 bootstrap에는 계속 사용하되, target state의 manager 판단은 `organization_memberships.organization_auth_state`를 기준으로 한다.

호환 순서:

```text
1. organization_memberships row가 active이고 organization_auth_state='manager'이면 manager
2. organization_memberships row가 invited/suspended/removed이면 fail-closed
3. membership row 자체가 없고 legacy 호환으로 organization.created_by 또는 organization.managed_by이면 manager
4. 둘 다 아니면 manager 아님
```

이 fallback은 MVP 1 데이터 회귀를 막기 위한 안전장치다. 일반 user의 resource 접근은 migration/backfill된 active organization membership을 통해 보장하고, `created_by`/`managed_by` fallback은 owner/manager 계정에만 적용한다.

### 6.5 기존 team permission 계산은 그대로 둔다

MVP 2-0은 team permission query를 새로 설계하지 않는다.

유지하는 것:

- `TeamMembership`과 `TeamWorkflowPermission` join
- `TeamMembership`과 `TeamLLMPermission` join
- user direct permission과 team permission 중 더 강한 `auth_state`를 선택하는 방식
- organization owner/manager의 resource manager override

추가하는 것:

- permission 계산 앞단에서 active organization membership을 확인한다.
- 단, migration/backfill 직후 기존 team member는 모두 active organization member가 되어야 하므로 정상 MVP 1 user에게 새 차단이 생기면 안 된다.

### 6.6 초대는 기존 user 기준으로 시작한다

MVP 2-0에서는 이미 가입한 user를 organization에 초대한다.

```text
POST /organizations/{organization_id}/members/invitations
{
  "user_id": "..."
}
```

이메일 기반 미가입 사용자 초대는 별도 후속 이슈로 둔다.

## 7. DB 모델

### 7.1 신규 table: `organization_memberships`

역할:

- user와 organization의 직접 소속 관계를 표현한다.
- active organization 목록 조회의 기준이다.
- organization manager 권한의 기준이다.
- team membership, user direct permission, MVP 2 knowledge permission의 전제 조건이다.

권장 column:

| Column | Type | Null | 설명 |
| --- | --- | --- | --- |
| `id` | UUID | No | primary key |
| `organization_id` | UUID | No | 소속 organization |
| `user_id` | UUID | No | 소속 user |
| `membership_state` | varchar(50) | No | `invited`, `active`, `suspended`, `removed` |
| `organization_auth_state` | varchar(50) | No | `member`, `manager` |
| `invited_by` | UUID | Yes | 초대한 user |
| `invited_at` | timestamptz | Yes | 초대 시각 |
| `accepted_at` | timestamptz | Yes | 수락 시각 |
| `removed_at` | timestamptz | Yes | 제거 시각 |
| `created_at` | timestamptz | No | 생성 시각 |
| `updated_at` | timestamptz | No | 수정 시각 |
| `options` | jsonb | No | 확장 metadata |
| `flags` | bigint | No | bit flag 확장 |

권장 constraint:

```text
PK: id
UQ: (organization_id, user_id)
FK: organization_id -> organization.id
FK: user_id -> users.id
FK: invited_by -> users.id
CHECK: membership_state in ('invited', 'active', 'suspended', 'removed')
CHECK: organization_auth_state in ('member', 'manager')
CHECK: flags >= 0
```

권장 index:

```text
ix_organization_memberships_organization_id
ix_organization_memberships_user_id
ix_organization_memberships_membership_state
ix_organization_memberships_org_state
ix_organization_memberships_user_state
```

### 7.2 상태 정의

| `membership_state` | 의미 | resource 접근 | team 추가 | direct permission 대상 |
| --- | --- | --- | --- | --- |
| `invited` | 초대됨, 아직 수락하지 않음 | 불가 | 불가 | 불가 |
| `active` | 활성 organization member | 가능성 있음 | 가능 | 가능 |
| `suspended` | 일시 정지 | 불가 | 불가 | 불가 |
| `removed` | 제거됨 | 불가 | 불가 | 불가 |

MVP 2-0 권장:

- invitation 생성: 없으면 insert, `removed`면 update로 재초대
- member 제거: `removed`로 soft remove
- permission cleanup: 제거 시 resource permission row와 team membership은 hard delete

이 선택은 의도적이다.

| 대상 | 처리 | 이유 |
| --- | --- | --- |
| `organization_memberships` | soft remove | invite/remove 이력과 last-manager guard 판단에 필요하다. |
| `team_memberships` | hard delete | 현재 membership table에 active state가 없고, 제거된 user가 team permission을 다시 받으면 안 된다. |
| `user_*_permissions` | hard delete | resource permission table에 active state가 없고, 재초대 시 과거 권한이 자동 복구되면 보안 문제가 된다. |

재초대 후 권한이 필요하면 manager가 team 배정 또는 user direct permission을 다시 부여한다.

### 7.3 organization auth state

| `organization_auth_state` | 의미 |
| --- | --- |
| `member` | organization에 소속된 일반 user |
| `manager` | organization/team/member/resource permission을 관리할 수 있는 user |

주의:

- `organization_auth_state`는 workflow/LLM/knowledge resource `auth_state`와 다르다.
- resource `auth_state`는 `viewer/operator/builder/manager`를 유지한다.
- organization manager가 resource scope 안에서 manager override를 받는 기존 정책은 유지한다.

## 8. 기존 table과의 관계

### 8.1 `team_memberships`

MBA-67 이전:

```text
team_memberships(grantee_organization_id, user_id, team_id)
```

변경 후:

- `grantee_organization_id`는 유지한다.
- migration 이후 모든 `team_memberships` row는 대응하는 active `organization_memberships` row가 있어야 한다.
- DB FK로 강제하기 어렵다면 service layer와 테스트에서 강제한다.
- 신규 team membership 생성 시 active organization membership이 없으면 400을 반환한다.

주의:

- 기존 `team_memberships` schema에는 `is_active`가 없다.
- MVP 2-0에서는 team membership 제거를 hard delete로 유지한다.
- membership 이력을 보존해야 하는 요구가 생기면 별도 `team_membership_events` 또는 audit metadata로 처리하고, MVP 2-0 table을 확장하지 않는다.

### 8.2 `user_*_permissions`

현재:

```text
user_workflow_permissions(grantee_organization_id, user_id, workflow_id)
user_llm_permissions(grantee_organization_id, user_id, llm_credential_id)
```

MVP 2에서 추가:

```text
user_knowledge_permissions(grantee_organization_id, user_id, knowledge_base_id)
```

변경 후:

- 모든 user direct permission은 active organization membership을 전제로 한다.
- team membership은 더 이상 user direct permission의 전제 조건이 아니다.
- organization에서 user가 제거되면 해당 organization의 user direct permission을 revoke한다.

MVP 1 대비 실제 변화:

| 항목 | MVP 1 | MVP 2-0 |
| --- | --- | --- |
| user direct permission 부여 대상 | target organization의 team member | target organization의 active organization member |
| team membership 없는 user direct grant | 불가 | 가능 |
| organization member가 아닌 user direct grant | 불가 | 불가 |

이 변화는 기존 동작을 좁히지 않고, "team에는 넣지 않지만 특정 user에게 resource 권한을 주는" 경우를 허용하는 확장이다.

### 8.3 `organization.created_by`, `organization.managed_by`

역할:

- legacy 호환
- migration backfill 기준
- organization bootstrap 기준

최종적으로는 `organization_memberships`를 manager source로 사용한다.

단, MVP 2-0에서는 기존 컬럼을 제거하지 않는다.

## 9. Migration 계획

### 9.1 Revision 추가

새 Alembic revision을 추가한다.

권장 이름:

```text
mvp2_0_add_organization_memberships
```

### 9.2 Upgrade 순서

1. `organization_memberships` table 생성
2. index/constraint 생성
3. 기존 `team_memberships`에서 distinct `(grantee_organization_id, user_id)`를 active member로 backfill
4. `organization.created_by`를 active manager로 backfill
5. `organization.managed_by`가 있으면 active manager로 backfill
6. 중복 row는 `(organization_id, user_id)` unique 기준으로 merge
7. created_by/managed_by로 manager가 된 row는 `accepted_at=created_at` 또는 migration 실행 시각으로 설정
8. team membership에서 생성된 row는 `organization_auth_state='member'`
9. 기존 organization owner가 team membership 없이 존재하던 경우도 active manager membership을 보장
10. backfill 결과 검증 query를 실행하고, 누락이 있으면 migration을 실패시킨다.

검증 query 기준:

```sql
-- team membership은 반드시 active organization membership을 가져야 한다.
SELECT tm.id
FROM team_memberships tm
LEFT JOIN organization_memberships om
  ON om.organization_id = tm.grantee_organization_id
 AND om.user_id = tm.user_id
 AND om.membership_state = 'active'
WHERE om.id IS NULL;

-- organization creator는 active manager membership을 가져야 한다.
SELECT o.id
FROM organization o
LEFT JOIN organization_memberships om
  ON om.organization_id = o.id
 AND om.user_id = o.created_by
 AND om.membership_state = 'active'
 AND om.organization_auth_state = 'manager'
WHERE om.id IS NULL;
```

두 query가 row를 반환하면 MVP 1 회귀 위험이 있으므로 migration을 성공 처리하지 않는다.

### 9.3 Backfill 세부 규칙

team membership 기반:

```sql
SELECT DISTINCT grantee_organization_id, user_id
FROM team_memberships
```

결과 row:

```text
organization_id = grantee_organization_id
user_id = user_id
membership_state = active
organization_auth_state = member
invited_by = assigned_by가 있으면 가장 이른 assigned_by, 없으면 organization.created_by
invited_at = 가장 이른 assigned_at
accepted_at = 가장 이른 assigned_at
```

organization creator 기반:

```text
organization_id = organization.id
user_id = organization.created_by
membership_state = active
organization_auth_state = manager
invited_by = organization.created_by
invited_at = organization.created_at
accepted_at = organization.created_at
```

organization manager 기반:

```text
organization_id = organization.id
user_id = organization.managed_by
membership_state = active
organization_auth_state = manager
invited_by = organization.created_by
invited_at = organization.created_at
accepted_at = organization.created_at
```

충돌 처리:

| 기존 row | 새 backfill | 결과 |
| --- | --- | --- |
| member active | manager active | manager로 승격 |
| manager active | member active | manager 유지 |
| removed | active backfill | active로 복구하지 않음. migration 시점에는 removed row가 없으므로 후속 운영 정책에만 적용 |

### 9.4 Legacy compatibility guard

MVP 1에서 생성된 데이터가 migration 이후에도 일부 누락될 수 있는 상황은 MBA-66 backfill과 검증으로 처리한다. MBA-67의 read/helper 경로는 legacy team membership이나 owner/manager 관계를 보고 organization membership row를 runtime에서 자동 생성하지 않는다.

적용 위치:

- `ensure_user_default_organization()`
- `get_user_primary_organization_id()`
- organization manager permission helper

동작:

```text
1. active organization membership이 있으면 그대로 사용한다.
2. invited/suspended/removed membership row가 있으면 fail-closed 처리한다.
3. membership row 자체가 없고 organization.created_by/managed_by이면 manager fallback으로만 판정한다.
4. legacy team membership만 있으면 backfill 누락 또는 데이터 불일치로 보고 read/helper 경로에서 row를 생성하지 않는다.
5. 명시적 bootstrap 경로인 ensure_user_default_organization()에서만 새 default organization과 active manager organization membership row를 생성한다.
```

이 guard는 migration 누락으로 기존 MVP 1 사용자가 scope를 잃는 문제를 런타임 쓰기로 숨기지 않고, 권한 helper를 fail-closed로 유지하기 위한 것이다.

운영 안정화 후 guard를 제거할 수 있지만, MVP 2-0 구현 직후에는 유지한다.

### 9.5 Downgrade

Downgrade는 table drop까지 지원한다.

주의:

- downgrade 시 `organization_memberships`로만 표현되던 초대 정보는 유실된다.
- 기존 `team_memberships`는 유지되므로 MVP 1 수준의 동작은 복구 가능하다.

## 10. Shared model 작업

### 10.1 SQLAlchemy model 추가

파일 후보:

```text
apps/shared/db/models/organization.py
```

또는 organization membership이 커질 경우:

```text
apps/shared/db/models/organization_membership.py
```

권장 class:

```text
OrganizationMembership
```

필수 constant:

```text
ORGANIZATION_MEMBERSHIP_INVITED = "invited"
ORGANIZATION_MEMBERSHIP_ACTIVE = "active"
ORGANIZATION_MEMBERSHIP_SUSPENDED = "suspended"
ORGANIZATION_MEMBERSHIP_REMOVED = "removed"

ORGANIZATION_AUTH_MEMBER = "member"
ORGANIZATION_AUTH_MANAGER = "manager"
```

### 10.2 relationship

권장 relationship:

```text
Organization.memberships
User.organization_memberships
OrganizationMembership.organization
OrganizationMembership.user
OrganizationMembership.inviter
```

relationship은 테스트 편의와 API response 조립에만 사용하고, permission helper에서는 명시 query를 우선 사용한다.

## 11. Permission helper 변경

### 11.1 신규 helper

`apps/shared/services/permissions.py`에 다음 helper를 추가한다.

```text
get_organization_membership(db, user_id, organization_id)
has_active_organization_membership(db, user_id, organization_id)
get_organization_auth_state(db, user_id, organization_id)
has_organization_manager_permission(db, user_id, organization_id)
```

판정 규칙:

```text
1. user_id와 organization_id를 UUID로 coerce한다.
2. organization이 active인지 확인한다.
3. organization_memberships row 존재 여부를 확인한다.
4. active row면 organization_auth_state를 읽어 member 또는 manager를 반환한다.
5. invited/suspended/removed row면 none을 반환한다.
6. membership row 자체가 없고 created_by/managed_by fallback이면 manager를 반환한다.
7. 그 외에는 none을 반환한다.
```

### 11.2 resource permission 계산 전제

workflow permission:

```text
1. workflow scope organization 확인
2. user의 active organization membership 확인
3. organization manager면 manager 반환
4. team permission 계산
5. user direct permission 계산
6. 가장 강한 resource auth_state 반환
```

LLM credential permission:

```text
1. credential scope organization 확인
2. user의 active organization membership 확인
3. organization manager면 manager 반환
4. team permission 계산
5. user direct permission 계산
6. 가장 강한 resource auth_state 반환
```

MVP 2 knowledge permission:

```text
1. knowledge base scope organization 확인
2. user의 active organization membership 확인
3. organization manager면 manager 반환
4. team_knowledge_permissions 계산
5. user_knowledge_permissions 계산
6. use/read/write/manage 허용 여부 반환
```

### 11.3 fail-closed 정책

다음 경우는 모두 거부한다.

- organization이 inactive
- active organization membership이 없음
- membership_state가 `invited`
- membership_state가 `suspended`
- membership_state가 `removed`
- resource organization과 requested organization이 불일치
- user가 deactivated

예외:

- `organization.created_by` 또는 `organization.managed_by`인 legacy owner/manager는 membership row 자체가 누락된 경우에만 manager fallback을 허용한다.
- 이 예외는 MVP 1 회귀 방지를 위한 owner/manager 전용 fallback이다.
- 일반 user의 team/user direct permission은 active membership이 없으면 사용하지 않는다. 정상 migration 후에는 기존 team member가 모두 active membership을 가지므로 기존 MVP 1 happy path가 깨지지 않아야 한다.

## 12. Gateway service 변경

### 12.1 `organization_context.py`

현재:

```text
get_user_primary_organization_id()
  -> 첫 active team membership 반환
```

변경:

```text
get_user_primary_organization_id()
  -> 첫 active organization membership 반환
```

정렬 기준:

```text
organization_memberships.accepted_at asc nulls last
organization_memberships.created_at asc
```

`ensure_user_default_organization()` 변경:

1. active organization membership이 있으면 그대로 반환
2. active organization membership이 없으면 기존 membership row나 legacy team membership을 reconciliation하지 않는다.
3. organization 생성
4. organization_memberships active manager row 생성
5. default team 생성
6. default team membership row 생성

주의:

- organization membership 생성이 먼저다.
- team membership은 organization membership 존재를 전제로 한다.

### 12.2 organization member service 추가

신규 파일 후보:

```text
apps/gateway/services/organization_member_service.py
```

책임:

- organization member 목록 조회
- 기존 user 초대
- 초대 수락
- membership state 변경
- organization auth_state 변경
- organization member 제거
- 제거 시 team memberships/direct permissions cleanup

### 12.3 `team_service.py`

변경 전:

```text
_ensure_grantee_user_membership()
  -> team_memberships에 row가 있어야 user direct permission 부여 가능
```

변경 후:

```text
_ensure_grantee_organization_membership()
  -> organization_memberships active row가 있어야 user direct permission 부여 가능
```

team member 추가:

```text
add_membership()
  -> active organization membership 확인
  -> team membership 생성
```

team member 제거:

```text
remove_membership()
  -> team membership만 제거
  -> organization membership은 유지
```

organization member 제거:

```text
remove_organization_member()
  -> organization_memberships state removed
  -> 해당 organization의 team_memberships 제거
  -> 해당 organization의 user_*_permissions 제거
```

삭제는 하나의 transaction 안에서 처리한다.

권장 순서:

```text
1. 제거 대상이 마지막 manager인지 확인한다.
2. 제거 대상이 current user 자신인지 확인한다.
3. affected team_memberships count를 계산한다.
4. affected user_workflow_permissions count를 계산한다.
5. affected user_llm_permissions count를 계산한다.
6. MVP 2에서 user_knowledge_permissions가 있으면 count를 계산한다.
7. team_memberships를 delete한다.
8. user_*_permissions를 delete한다.
9. organization_memberships를 removed로 update한다.
10. audit log를 기록한다.
11. commit한다.
```

중간 실패 시 제거가 일부만 반영되면 안 된다.

MVP 3에서 `user_audit_permissions`가 추가된 뒤에는 이 table도 동일한 `user_*_permissions` cleanup 대상과 count에 포함한다.

### 12.4 permission grant/revoke

team grant:

- team이 target organization에 속해야 한다.
- team이 active여야 한다.
- team permission row 생성/수정.

user direct grant:

- user가 target organization active member여야 한다.
- team membership은 필요 없다.
- user direct permission row 생성/수정.

## 13. API 계약

### 13.1 Organization list

```text
GET /api/v1/organizations
```

Permission:

- authenticated

Response:

기존 MVP 1 frontend 호환을 위해 active organization context 후보인 `OrganizationResponse[]`를 반환한다. `invited`, `suspended`, `removed` membership은 포함하지 않는다.

```json
[
  {
    "id": "uuid",
    "name": "Acme",
    "options": {},
    "is_active": true,
    "is_manager": true,
    "created_at": "2026-06-29T00:00:00Z",
    "updated_at": "2026-06-29T00:00:00Z"
  }
]
```

조회 기준:

- current user의 active `organization_memberships`
- `membership_state == 'active'`
- 응답 항목은 `X-Organization-Id` active context 후보로 사용할 수 있다.

### 13.1.1 Organization membership list

```text
GET /api/v1/organizations/memberships
```

Permission:

- authenticated

Response:

```json
[
  {
    "id": "uuid",
    "name": "Acme",
    "membership_state": "active",
    "organization_auth_state": "manager",
    "is_active": true
  }
]
```

조회 기준:

- current user의 `organization_memberships`
- `membership_state in ('active', 'invited')`
- 초대 수락 UI에서는 active와 invited를 구분 표시한다.
- invited membership은 `X-Organization-Id` active context 후보로 저장하지 않는다.

### 13.2 Current organization

```text
GET /api/v1/organizations/current
```

Permission:

- authenticated

Response:

기존 Organization API 계약을 유지해 `OrganizationResponse`를 반환한다.

```json
{
  "id": "uuid",
  "name": "Acme",
  "options": {},
  "is_active": true,
  "is_manager": false,
  "created_at": "2026-06-29T00:00:00Z",
  "updated_at": "2026-06-29T00:00:00Z"
}
```

### 13.3 Member list

```text
GET /api/v1/organizations/{organization_id}/members
```

Permission:

- organization `manager`

Query:

```text
state=active|invited|suspended|removed
```

Response:

```json
[
  {
    "id": "membership_uuid",
    "organization_id": "uuid",
    "user_id": "uuid",
    "user_email": "user@example.com",
    "user_name": "User",
    "membership_state": "active",
    "organization_auth_state": "member",
    "invited_by": "uuid",
    "invited_at": "2026-06-28T00:00:00Z",
    "accepted_at": "2026-06-28T00:05:00Z"
  }
]
```

### 13.4 Invite existing user

```text
POST /api/v1/organizations/{organization_id}/members/invitations
```

Permission:

- organization `manager`

Request:

```json
{
  "user_id": "uuid",
  "organization_auth_state": "member"
}
```

Rules:

- 초대 대상 user가 존재해야 한다.
- deactivated user는 초대할 수 없다.
- 이미 active member이면 idempotent하게 기존 membership 반환.
- `removed` row가 있으면 `invited`로 재활성화한다.
- 이미 invited member이면 idempotent하게 기존 invitation을 반환하되 `invited_at`을 갱신하지 않는다.
- 이미 suspended member이면 409를 반환하고, reactivate는 `PATCH /members/{user_id}`로만 처리한다.
- 초대 생성자 자신에게 invite를 보내는 것은 no-op으로 처리하거나 400으로 막는다. MVP 2-0 권장은 400이다.
- `organization_auth_state='manager'` 초대는 현재 user가 organization manager일 때만 허용한다.

Response:

```json
{
  "id": "membership_uuid",
  "membership_state": "invited",
  "organization_auth_state": "member"
}
```

### 13.5 Accept invitation

권장 endpoint:

```text
POST /api/v1/organizations/{organization_id}/members/me/accept
```

호환 가능한 대체 endpoint:

```text
POST /api/v1/organizations/{organization_id}/members/{user_id}/accept
```

Permission:

- target user 본인
- 또는 organization manager가 강제 activate 가능하도록 할지는 MVP 2-0에서 제외

Rules:

- `/members/me/accept`를 우선 사용한다.
- `/members/{user_id}/accept`를 구현한다면 current user id가 path `user_id`와 같아야 한다.
- membership_state가 `invited`여야 한다.
- accept 후 `active`, `accepted_at=now`.
- 이미 active이면 idempotent하게 active membership을 반환한다.
- suspended/removed 상태는 accept할 수 없고 409를 반환한다.

Response:

```json
{
  "id": "membership_uuid",
  "membership_state": "active"
}
```

### 13.6 Update member

```text
PATCH /api/v1/organizations/{organization_id}/members/{user_id}
```

Permission:

- organization `manager`

Request:

```json
{
  "membership_state": "suspended",
  "organization_auth_state": "manager"
}
```

Rules:

- `membership_state`는 `active`, `suspended`만 직접 변경 가능.
- `removed`는 DELETE endpoint를 사용한다.
- 마지막 manager를 member/suspended/removed로 낮추는 것은 금지한다.
- 자기 자신의 manager 권한을 낮추는 것도 금지하거나 별도 확인 flow가 필요하다. MVP 2-0에서는 금지한다.
- invited member를 `active`로 바꾸는 manager-side 강제 수락은 MVP 2-0에서 허용하지 않는다. 초대 수락은 user 본인이 해야 한다.
- suspended member를 active로 되돌리면 suspend 중 유지되던 team membership과 user direct permission row가 다시 효력을 가진다.

### 13.7 Remove member

```text
DELETE /api/v1/organizations/{organization_id}/members/{user_id}
```

Permission:

- organization `manager`

Rules:

- 마지막 manager 제거 금지.
- 자기 자신 제거 금지.
- membership_state를 `removed`로 변경한다.
- `removed_at=now`.
- 해당 organization의 team membership을 제거한다.
- 해당 organization의 user direct permission을 제거한다.
- invited 상태의 user도 remove할 수 있다.
- removed 상태의 user에 대해 다시 DELETE가 오면 idempotent하게 `status='removed'`를 반환한다.
- audit log에 cleanup 대상 개수를 metadata로 남긴다.

Response:

```json
{
  "status": "removed",
  "removed_team_memberships": 3,
  "revoked_user_permissions": {
    "workflow": 2,
    "llm_credential": 1,
    "knowledge_base": 0,
    "audit": 0
  }
}
```

### 13.8 상태 전이표

MVP 2-0의 membership state 전이는 아래로 제한한다.

| 현재 상태 | Action | 다음 상태 | 허용 주체 | 비고 |
| --- | --- | --- | --- | --- |
| 없음 | invite | `invited` | organization manager | 기존 user만 가능 |
| `invited` | accept | `active` | invited user 본인 | `/members/me/accept` 권장 |
| `invited` | remove | `removed` | organization manager | 초대 취소 |
| `active` | suspend | `suspended` | organization manager | team/direct permission row는 삭제하지 않는다. helper가 접근을 차단한다. |
| `active` | remove | `removed` | organization manager | team/direct permission cleanup |
| `suspended` | reactivate | `active` | organization manager | 남아 있던 team/direct permission row가 다시 효력을 가진다. |
| `suspended` | remove | `removed` | organization manager | team/direct permission cleanup |
| `removed` | invite | `invited` | organization manager | 재초대, 과거 권한 복구 없음 |

Suspend 처리:

- MVP 2-0에서는 `suspended` 전환 시 team membership과 user direct permission을 삭제하지 않는다.
- permission helper가 suspended membership을 fail-closed 처리하므로 접근은 차단된다.
- reactivate하면 기존 team/direct permission이 다시 효력을 가진다.
- 이 동작이 부담되면 remove를 사용한다.

Remove 처리:

- `removed` 전환 시 team membership과 user direct permission을 삭제한다.
- 재초대 후 accept해도 과거 권한은 복구되지 않는다.
- 이는 의도적인 보안 기본값이다.

### 13.9 MVP 1 API 호환성

MVP 2-0에서 기존 MVP 1 API는 다음처럼 유지한다.

| 기존 API | 변경 여부 | 비고 |
| --- | --- | --- |
| `POST /api/v1/teams` | 유지 | organization manager 판정 내부만 membership 기준으로 확장 |
| `GET /api/v1/teams` | 유지 | request/response shape 유지 |
| `PATCH /api/v1/teams/{team_id}` | 유지 | manager 권한 판정만 확장 |
| `DELETE /api/v1/teams/{team_id}` | 유지 | team 비활성화 semantics 유지 |
| `POST /api/v1/teams/{team_id}/members` | 유지 | target user 검증만 organization membership 기준으로 변경 |
| `DELETE /api/v1/teams/{team_id}/members/{user_id}` | 유지 | organization membership은 제거하지 않음 |
| workflow team permission grant/revoke | 유지 | 기존 request/response 유지 |
| workflow user permission grant/revoke | 유지 | grantee 검증 기준만 team membership에서 organization membership으로 변경 |
| LLM credential team permission grant/revoke | 유지 | 기존 request/response 유지 |
| LLM credential user permission grant/revoke | 유지 | grantee 검증 기준만 team membership에서 organization membership으로 변경 |
| `GET /api/v1/organizations` | 유지 | 기존 response shape 유지, active membership만 반환 |
| `GET /api/v1/organizations/memberships` | 신규 | membership 상태 목록과 invited organization은 새 endpoint에서 제공 |

즉 MVP 1 client 코드는 가능한 한 수정하지 않고, 새 Members UI와 초대 수락 UI는 membership 전용 endpoint를 사용한다.

## 14. Schema 작업

신규 schema 후보:

```text
apps/shared/schemas/organization_membership.py
```

필요 schema:

```text
OrganizationMemberInviteRequest
OrganizationMemberUpdateRequest
OrganizationMemberResponse
OrganizationSummaryResponse
OrganizationMemberRemoveResponse
```

validation:

- `membership_state`는 허용값만 받는다.
- `organization_auth_state`는 `member`, `manager`만 받는다.
- invite request의 기본 auth_state는 `member`.

## 15. Audit 설계

MVP 2-0에서 audit에 남겨야 하는 action:

| Action | 시점 | status | metadata |
| --- | --- | --- | --- |
| `organization.invite` | organization invitation 생성 | success/failure | target user, organization, invited_by |
| `organization.member.accept` | 초대 수락 | success/failure | user, organization |
| `organization.member.update` | state/auth_state 변경 | success/failure | before/after |
| `organization.member.remove` | member 제거 | success/failure | cleanup count |
| `permission.revoke` | 제거로 인한 permission cleanup | success | revoked resource counts |

기존 audit action enum에 없는 경우 선택지:

1. `AuditAction`에 canonical action 추가
2. 기존 `permission.grant/revoke`와 별도 string action을 허용

권장:

- organization membership action은 명시 action으로 추가한다.
- permission cleanup은 기존 `permission.revoke`를 재사용하되 metadata에 `reason='organization.member.remove'`를 넣는다.

민감 정보:

- target user email은 audit metadata에 저장하지 않는다.
- 요청 수행자 복원력을 위해 `audit_metadata.actor` snapshot에는 actor id/email/name을 저장한다.
- target user id, organization id, membership id는 저장한다.

Audit metadata 예시:

```json
{
  "organization_id": "uuid",
  "membership_id": "uuid",
  "target_user_id": "uuid",
  "previous_membership_state": "active",
  "next_membership_state": "removed",
  "previous_organization_auth_state": "member",
  "next_organization_auth_state": "member",
  "reason": "organization.member.remove",
  "cleanup": {
    "team_memberships": 2,
    "user_workflow_permissions": 1,
    "user_llm_permissions": 1,
    "user_knowledge_permissions": 0,
    "user_audit_permissions": 0
  }
}
```

Audit 기록 시점:

- invite/update/remove 성공 후 같은 transaction 안에서 audit row를 생성한다.
- accept는 target user 본인의 action으로 기록한다.
- cleanup으로 삭제되는 개별 permission row마다 audit row를 남길지, aggregate row 하나만 남길지는 구현 복잡도에 따라 선택한다. MVP 2-0 권장은 aggregate row 하나다.

## 16. UI 계획

### 16.1 Organization settings

새 탭:

```text
Settings
  -> Organization
    -> Members
    -> Teams
    -> Permissions
```

Members 탭:

- member list
- state badge: invited, active, suspended
- organization auth badge: member, manager
- invite button
- suspend/reactivate action
- remove action

### 16.2 Invite modal

MVP 2-0 invite modal:

- 기존 user 검색
- organization auth 선택: member, manager
- invite submit

미지원:

- email-only invite
- bulk invite
- CSV import

### 16.3 Team member picker

변경 전:

- user id 직접 입력 또는 전체 user 선택

변경 후:

- active organization members만 표시
- invited/suspended/removed user는 선택 불가
- team에 없는 active member만 add 가능

### 16.4 Resource permission user picker

변경 후:

- active organization members만 표시
- team membership 여부와 무관하게 표시
- 이미 direct permission이 있으면 현재 auth_state 표시

### 16.5 Active organization switcher

MVP 2-0에서 다중 organization switcher를 넣을 수 있다면 다음 기준을 따른다.

- active organization switcher는 `GET /api/v1/organizations`를 사용해 active membership organization만 표시한다.
- 초대 수락 UI는 `GET /api/v1/organizations/memberships`를 사용해 invited organization을 "초대됨" 상태로 표시하되 resource 화면으로 진입하지 않는다.
- active organization 변경은 선택한 organization id를 client state/localStorage 등에 저장하고, 이후 organization-scoped 요청에 `X-Organization-Id` header로 전달한다.

다중 switcher가 MVP 2-0 범위를 넘으면 최소한 현재 organization 표시와 organization list API만 추가한다.

## 17. MVP 2 기능과의 연결

MVP 2의 주요 작업은 다음이다.

```text
knowledge base/document use enforcement
RAG retrieval trace metadata
data classification
re-index flow
audit log search
```

`organization_memberships`는 그중 permission enforcement의 선행 조건이다.

아래 병렬 작업 제한은 원 계획 작성 당시의 foundation 완료 전 gate였다. Dev 기준으로 `organization_memberships` DB/model/migration/backfill과 permission helper 전환이 완료됐으므로, 현재는 `user_knowledge_permissions`, knowledge base `use` permission helper, RAG execution fail-closed enforcement를 MVP 2/MBA-75 구현 단위에서 진행할 수 있다.

foundation 완료 전 허용됐던 병렬 작업:

- RAG trace payload shape 초안
- audit search UI mock
- re-index UI mock
- test fixture 설계

foundation 완료 전 금지됐던 병렬 작업:

- `user_knowledge_permissions` migration 확정
- knowledge base `use` permission helper 확정
- RAG execution fail-closed enforcement

당시 이유는 user direct knowledge permission의 grantee 검증 기준이 `organization_memberships`에 의존하기 때문이었다. 현재 구현에서는 이 전제 계층이 준비됐으므로, 남은 판단은 `user_knowledge_permissions`를 MBA-75에 포함할지 별도 이슈로 나눌지에 관한 구현 단위 결정이다.

### 17.1 `user_knowledge_permissions`

MVP 2에서 `user_knowledge_permissions`를 추가할 때 기준은 다음이다.

```text
user_knowledge_permissions.grantee_organization_id
  -> organization_memberships.organization_id

user_knowledge_permissions.user_id
  -> organization_memberships.user_id
```

service layer rule:

```text
user direct knowledge permission grant는 active organization member에게만 가능
```

### 17.2 RAG execution

RAG node 실행 전:

```text
1. execution context user_id 확인
2. execution context organization_id 확인
3. active organization membership 확인
4. knowledge base use permission 확인
5. document classification policy 확인
6. allow/warn/block decision 기록
```

### 17.3 Audit search

MVP 2 audit search에는 organization membership event도 포함한다.

검색 필터:

- `organization.invite`
- `organization.member.accept`
- `organization.member.update`
- `organization.member.remove`
- `permission.revoke` with reason

## 18. 구현 이슈 분해

### Issue 0-1. `[DB][RBAC] organization_memberships 모델과 migration 추가`

작업:

- SQLAlchemy model 추가
- Alembic migration 작성
- backfill SQL 작성
- downgrade 작성
- model import 경로 정리

Acceptance Criteria:

- migration upgrade 후 모든 기존 team member가 organization active member가 된다.
- organization creator/manager는 manager membership을 가진다.
- `(organization_id, user_id)` 중복이 없다.
- downgrade가 table drop까지 수행된다.

MBA-66 구현 메모:

- `organization_memberships` DB/model/migration foundation을 추가한다.
- schema migration과 data/backfill migration을 분리한다.
- MBA-67에서 permission helper와 일부 API endpoint 전환이 진행됐다.
- MBA-71에서 active organization과 manager/member 화면 분기 일부가 반영됐다.
- Organization member/invitation BE API는 구현됐고, full membership 관리 UI는 후속 범위로 유지한다.

### Issue 0-2. `[BE][RBAC] organization membership 기반 permission helper 전환`

작업:

- `has_active_organization_membership` 추가
- `get_organization_auth_state` 추가
- `has_organization_manager_permission` 전환
- workflow/LLM permission helper 전제 조건 추가
- legacy fallback 테스트 추가

Acceptance Criteria:

- active member가 아니면 resource permission row가 있어도 접근 거부된다.
- manager membership은 organization scope 안에서 manager로 판정된다.
- membership row 자체가 없는 created_by/managed_by legacy fallback이 유지된다.
- team permission 계산은 기존과 동일하게 동작한다.

### Issue 0-3. `[BE][API] organization member/invitation API 추가` (BE 완료)

작업:

- organization member service 추가
- list/invite/accept/update/remove endpoint 추가
- schema 추가
- audit 기록 추가
- self-removal/last-manager guard 추가

Acceptance Criteria:

- manager가 기존 user를 invite할 수 있다.
- invited user는 직접 accept할 수 있다.
- invited user는 accept 전 resource 접근이 불가능하다.
- manager는 member를 suspend/reactivate/remove할 수 있다.
- 마지막 manager 제거가 차단된다.

### Issue 0-4. `[BE][RBAC] team membership과 user direct permission 검증 기준 변경`

작업:

- team add 시 active organization membership 확인
- user direct workflow/LLM permission grant 시 organization membership 확인
- `_ensure_grantee_user_membership` 제거 또는 의미 변경
- member removal cleanup 구현

Acceptance Criteria:

- team에 속하지 않은 active organization member에게 direct permission을 줄 수 있다.
- organization member가 아닌 user에게 direct permission을 줄 수 없다.
- organization member가 아닌 user를 team에 추가할 수 없다.
- organization member 제거 시 team membership과 direct permission이 정리된다.

### Issue 0-5. `[FE] Organization Members UI 추가`

작업:

- organization members list
- invite modal
- accept invitation entry
- member state/auth_state update
- remove action
- team member picker 변경
- direct permission user picker 변경

Acceptance Criteria:

- UI에서 user를 organization에 초대할 수 있다.
- invited/active/suspended 상태가 구분된다.
- active member만 team에 추가할 수 있다.
- active member만 direct permission 대상으로 표시된다.

### Issue 0-6. `[Test][QA] MVP2-0 regression`

작업:

- migration test
- permission helper unit test
- organization member API test
- team membership API regression
- user direct permission regression
- MVP1 demo smoke 재검증

Acceptance Criteria:

- 기존 MVP1 workflow/LLM permission 흐름이 깨지지 않는다.
- 신규 organization membership 흐름이 API와 UI에서 통과한다.
- member removal 후 권한이 즉시 차단된다.

## 19. 테스트 상세

### 19.1 Migration test

시나리오:

1. organization 생성
2. team 생성
3. team membership 2개 생성
4. creator는 team membership 없음
5. migration 실행
6. organization memberships 확인

기대:

- team member 2명 active member
- creator active manager
- 중복 없음

### 19.2 Permission helper test

시나리오:

| 조건 | 기대 |
| --- | --- |
| active member + team viewer | workflow viewer |
| no organization membership + team viewer row만 존재 | none |
| active manager membership | manager |
| invited membership + user direct builder | none |
| suspended membership + team manager | none |
| active member + user direct builder + no team | builder |

### 19.3 API test

시나리오:

1. manager invites user
2. invited user cannot access workflow
3. invited user accepts
4. manager grants workflow direct builder
5. user can access workflow
6. manager removes user
7. user can no longer access workflow
8. direct permission row is removed

### 19.4 Team API regression

시나리오:

- active organization member can be added to team
- invited member cannot be added to team
- non-member cannot be added to team
- team removal does not remove organization membership

### 19.5 MVP2 knowledge permission 준비 test

MVP 2 본작업에서 추가할 테스트의 선행 fixture:

- active organization member without team
- user direct knowledge permission
- knowledge base use allowed
- removed member blocked

## 20. Rollout 계획

### 20.1 1단계: Dual-read

초기 배포:

- organization membership을 생성한다.
- active organization 조회는 membership 기준으로 전환한다.
- 단, membership row 자체가 없는 owner/manager legacy fallback을 유지한다.

### 20.2 2단계: Write-through

신규 organization/team 생성:

- organization membership을 먼저 생성한다.
- team membership은 active membership user에게만 생성한다.

### 20.3 3단계: Enforce

permission helper:

- active organization membership 없으면 일반 resource permission은 fail-closed.
- legacy fallback은 membership row 자체가 없는 creator/managed_by에 한정한다.

### 20.4 4단계: Cleanup

후속 작업:

- "사용자 소속 = team_memberships" 표현을 폐기된 과거 전제로 정리
- team membership 기반 primary organization fallback 제거
- email invitation 확장 검토

## 21. 문서 수정 범위

MVP 2-0 구현 시 함께 수정해야 할 문서:

| 문서 | 수정 내용 |
| --- | --- |
| `docs/data-model/physical-data-model.md` | `organization_memberships` table 추가, 사용자 소속 기준 변경 |
| `docs/data-model/rbac-permission-policy.md` | organization membership 전제 조건 추가 |
| `docs/data-model/diagrams/rbac-relationships.md` | organization-user 직접 관계 추가 |
| `docs/data-model/diagrams/data-model-overview.md` | organization memberships 관계 추가 |
| `docs/api/organization-rbac.md` | organization member/invitation BE API 계약 반영 |
| `docs/requirements/mvp-2-governance-rag-audit.md` | MVP2 선행 조건으로 organization membership 추가 |
| `docs/architecture/auth-rbac.md` | active organization context source 변경 |
| `docs/api/auth.md` | active organization 전달 계약이 바뀌는 경우 request/header/session 설명 갱신 |
| `docs/implementation-plan/risk-consistency-verification.md` | MVP 2-0 반영 후 current/target 정합성 결과 갱신 |

결정 기록 문서는 구현 계획의 정합성 갱신 대상에 포함하지 않고, MVP 2-0 구현에 따른 현재 계획/요구사항/API/data-model 정합성은 위 active 문서들에 반영한다.

## 22. 완료 기준

아래 표는 원 계획의 전체 완료 기준과 현재 PR 기준 상태를 함께 기록한다.

| 영역 | 원 계획 완료 기준 | 현재 PR 기준 상태 |
| --- | --- | --- |
| DB | `organization_memberships` table과 migration/backfill 완료 | 완료 |
| Organization context | active organization 조회가 organization membership 기준으로 동작 | helper/API 전환 범위 완료. Legacy fallback 축소는 후속 |
| Invitation | manager가 기존 user를 organization에 invite 가능 | BE 완료. Full membership UI는 후속 |
| Acceptance | user가 invite를 accept하여 active member가 됨 | BE 완료. Full membership UI는 후속 |
| Team | active organization member만 team에 추가 가능 | 완료된 foundation 위에서 유지 |
| User direct permission | team 소속이 없어도 active organization member면 direct permission 가능 | workflow/LLM permission 전환 범위 완료. Knowledge/audit user direct permission은 MVP 2/3 후속 |
| Enforcement | active organization member가 아니면 resource permission row가 있어도 접근 차단 | workflow/LLM permission helper 전환 범위 완료. KB/RAG enforcement는 MVP 2 후속 |
| Cleanup | organization member 제거 시 team/direct permission 정리 | BE 완료. Full membership UI 연동은 후속 |
| Audit | invite/accept/update/remove/cleanup event 기록 | BE 완료. Full membership UI 연동은 후속 |
| UI | members list, invite, state 표시, team/direct permission picker 반영 | full membership UI는 남음. MBA-71의 manager/member 화면 분기 일부만 반영 |
| Regression | MVP1 workflow/LLM permission demo가 계속 통과 | 완료된 foundation 변경의 회귀 기준으로 유지 |

## 23. Demo script

```text
1. organization manager로 로그인한다.
2. Organization Settings > Members에서 기존 user를 invite한다.
3. 초대받은 user로 로그인한다.
4. 초대 상태에서는 workflow 접근이 되지 않는 것을 확인한다.
5. 초대를 accept한다.
6. manager가 해당 user를 team에는 넣지 않고 workflow direct builder 권한을 부여한다.
7. user가 workflow를 조회/수정/실행할 수 있음을 확인한다.
8. manager가 user를 team에 추가한다.
9. team permission도 정상 반영되는지 확인한다.
10. manager가 user를 organization에서 제거한다.
11. user의 workflow 접근이 즉시 차단되는지 확인한다.
12. audit/activity에서 invite, accept, permission grant, remove, cleanup event를 확인한다.
```

## 24. 리스크와 대응

| 리스크 | 영향 | 대응 |
| --- | --- | --- |
| migration backfill 누락 | 기존 user가 organization을 잃음 | team membership, created_by, managed_by를 모두 backfill source로 사용 |
| organization membership과 team membership 불일치 | 권한 계산 혼란 | service layer에서 team add 전 active membership 강제 |
| removed user의 direct permission 잔존 | 보안 문제 | removal transaction에서 team/direct permission cleanup |
| 마지막 manager 제거 | organization 관리 불가 | last-manager guard |
| invited user 접근 허용 | 초대 수락 전 정보 노출 | membership_state active만 permission helper에서 허용 |
| active organization 전달 계약 불명확 | client/server 계약 혼란 | organization membership 기준으로 active API/architecture 문서를 갱신하고 header/session/cookie 중 사용할 방식을 확정 |
| MVP1 회귀 | 기존 demo 실패 | MVP1 workflow/LLM permission regression을 MVP2-0 완료 기준에 포함 |

## 25. 결정 필요/완료 이력

아래 항목은 아직 결정이 필요한 선택지와 구현 과정에서 canonical ADR로 확정된 항목을 함께 기록한다. MVP 2-0 문서는 MVP 1 변경을 최소화하는 기본값도 함께 제시하므로, 결정 전에도 구현 계획을 읽고 이슈를 나눌 수 있다.

| 항목 | 기본값 | 결정이 필요한 순간 | 영향 |
| --- | --- | --- | --- |
| active organization 전달 방식 | 기존 방식 유지. membership 기반 primary organization 조회로만 교체 | 다중 organization switcher 또는 explicit active organization 변경 UI를 구현하기 직전 | API header/session/cookie 계약, FE request scope |
| invitation 대상 | 기존 가입 user만 초대 | email-only invite를 MVP 2에 포함하려는 순간 | invitation token table, email 발송, signup completion flow |
| accept endpoint | `POST /organizations/{organization_id}/members/me/accept` | endpoint path를 API spec에 확정하기 직전 | client API shape |
| suspended member 권한 row 처리 | row 유지, helper에서 차단 | suspend를 "임시 차단"이 아니라 "권한 제거"로 쓰고 싶을 때 | reactivate 시 권한 복구 여부 |
| removed member 권한 row 처리 | team/direct permission hard delete | 제거 후 재초대 시 과거 권한 복구를 원할 때 | 보안 기본값, audit/restore UX |
| organization manager 초대 | manager invite 허용 | manager 권한 부여를 별도 approval로 제한하려는 순간 | admin UX, last-manager policy |
| organization membership audit action | 결정 완료: `organization.invite`, `organization.member.accept/update/remove`, cleanup `permission.revoke` | canonical audit action ADR 변경 전 | audit action vocabulary |

현재 권장 기본값은 모두 "MVP 1을 덜 건드리는 방향"이다.

특히 지금 당장 사용자 결정이 필요한 것은 없다. 실제 구현 착수 시점에 위 항목 중 하나를 기본값과 다르게 가져가려면 다시 확인한다.

## 26. 권장 구현 순서

```text
MVP2-0 Issue 0-1 DB/model/migration
  -> Issue 0-2 permission helper 전환
  -> Issue 0-3 organization member API
  -> Issue 0-4 team/direct permission 검증 기준 변경
  -> Issue 0-5 UI
  -> Issue 0-6 regression
  -> MVP2 Data Source Permission Enforcement
```

MVP 2의 code-level 권한 구현은 Issue 0-2와 Issue 0-4가 끝난 뒤 시작할 수 있다. 전체 Data Source Permission Enforcement 작업은 Issue 0-6 regression이 끝난 뒤 시작한다.

특히 `user_knowledge_permissions`, knowledge base `use` enforcement, RAG node execution check는 organization membership 전제가 고정된 후 구현한다.
