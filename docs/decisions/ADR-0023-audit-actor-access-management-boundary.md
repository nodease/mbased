# ADR-0023: Audit Actor Access Management Boundary

Status: Accepted
Related ADRs: [ADR-0006](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0009](ADR-0009-active-organization-header-context.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0016](ADR-0016-permission-request-and-app-creation-permission.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md)

## Context

Organization manager는 기존 Admin Console의 member tab에서 member를 정지·재활성화하고 organization role을 변경할 수 있다. Resource permission tab에서는 workflow, Knowledge Base, LLM credential의 team/user direct permission을 resource 중심으로 관리할 수 있다. Audit tab은 organization audit log를 검색하고 상세를 조회하지만 actor는 표시값일 뿐 관리 대상과 연결되지 않는다.

MBA-188은 audit actor에서 현재 organization member의 access 상태를 확인하고 정지·역할·리소스 권한을 항목별로 제어하는 흐름을 추가한다. 이때 다음 경계를 결정해야 한다.

- audit `auditor` 권한이 member/permission mutation 권한도 의미하는지
- actor management business rule을 Audit, Organization, Permission 중 어디가 소유하는지
- suspension과 permission revoke/restore를 어떻게 구분하는지
- team-inherited permission을 user direct deny로 상쇄할지
- organization manager override 대상의 개별 resource revoke를 허용할지
- 관리 mutation과 audit 기록을 어떤 transaction으로 보장할지
- AuditLog의 generic `before`/`after`를 관리 UI에 어떻게 안전하게 노출할지

## Options

### Option 1. Audit reader가 actor mutation을 직접 수행

Audit service와 Admin audit endpoint가 member와 permission table을 직접 변경한다.

- 장점: audit UI에서 구현 경로가 짧다.
- 단점: `auditor` visibility가 RBAC mutation capability로 상승하고 Audit 도메인에 Organization/Permission 정책과 transaction이 결합된다.

### Option 2. Client가 기존 endpoint를 직접 조합

Audit UI가 기존 member, team, permission endpoint를 action별로 호출한다.

- 장점: backend 신규 API가 적다.
- 단점: actor-centric read model이 없고 reason, manager override, permission source, transaction-bound audit 정책이 endpoint마다 갈라진다.

### Option 3. Access Management application boundary 도입

Audit UI는 actor id를 진입점으로 사용하지만, Gateway의 access-management use case가 member scope, permission source, mutation transaction과 audit port 호출을 조율한다.

- 장점: audit read와 mutation authority를 분리하고 ADR-0022의 application/port/adapter 경계를 실제 permission use case에 적용할 수 있다.
- 단점: legacy service와 신규 use case가 과도기 동안 공존한다.

## Decision

Option 3을 채택한다.

감사 로그는 actor management 진입점과 read model을 제공하지만 organization member와 resource access mutation의 업무 정책을 소유하지 않는다. Gateway `access_management` application boundary가 Organization/RBAC policy, resource permission registry, repository port, audit recorder port, UnitOfWork를 조율한다.

Application port는 하나의 거대한 access repository로 합치지 않는다. Actor access query, membership/global-user lock, team membership, user-direct resource permission, App creation permission, resource scope/catalog를 capability별 protocol로 분리한다. 하나의 SQLAlchemy adapter가 여러 protocol을 구현할 수 있지만 use case는 필요한 최소 port에만 의존한다.

Listener suppression은 SQLAlchemy adapter의 내부 책임이다. Repository adapter는 tracked object를 dirty/add/delete 상태로 만들기 전에 manual ownership을 등록하고, application에는 canonical action/category/target/before/after로 구성된 framework-independent mutation descriptor만 반환한다. Audit recorder port에 ORM object나 listener token을 노출하지 않는다.

```text
Audit/Admin inbound adapter
  -> Access Management application use case
       -> Organization/RBAC policy
       -> Capability-specific member/permission ports
       -> Audit recorder port
       -> UnitOfWork
  -> SQLAlchemy/audit outbound adapters
```

## Authorization Boundary

Audit visibility와 access mutation capability를 분리한다.

| Capability | Required permission |
| --- | --- |
| organization audit list/detail | audit `auditor` 이상 |
| actor access profile/resource source 조회 | ADR-0009 organization `manager` 판정 |
| membership, role, team, direct resource, App creation mutation | ADR-0009 organization `manager` 판정 |

`auditor`와 `raw_auditor`는 organization manager가 아니면 actor management profile과 mutation API에 접근할 수 없다. Manager 판정은 ADR-0009의 active membership 우선 규칙과 membership row가 없을 때만 허용되는 legacy `created_by`/`managed_by` fallback을 그대로 사용한다. Frontend 버튼 노출은 UX 보조이며 Gateway가 최종 보안 경계다.

System actor, actor id가 없는 event, current organization에서 active 또는 suspended membership이 없는 invited/removed/historical actor는 audit detail만 표시한다. Actor id만으로 target scope를 신뢰하지 않고 path/header organization과 target membership을 서버에서 다시 검증한다. Globally deactivated user는 active/suspended membership이 있으면 cleanup profile을 반환하지만 effective access를 disabled로 계산한다.

## Access Policy

### Suspension

- 정지는 global user account가 아니라 current organization membership을 `suspended`로 변경한다.
- Team membership, user direct permission, App creation permission row는 보존한다.
- Organization scope 판정은 active global user와 active membership을 선행 조건으로 사용하므로 globally deactivated 또는 suspended member의 effective access는 fail-closed다.
- 재활성화는 보존된 permission source를 다시 평가한다.
- Suspended target은 재활성화와 cleanup만 허용한다. Manager-to-member 강등, team remove, direct/App creation revoke는 허용하지만 member-to-manager 승격, team add, direct/App creation grant는 재활성화 뒤 수행한다.
- 기존 session 즉시 종료와 이미 실행 중인 workflow 중단은 이 결정의 범위가 아니다.

### Permission source and restore

- User direct permission은 ADR-0006의 additive allow를 유지하고 explicit deny를 도입하지 않는다.
- Team-inherited access는 source team과 auth_state를 표시한다. 특정 user의 inherited access를 회수하거나 복구할 때는 team membership을 제거하거나 다시 추가한다.
- Inactive team membership은 stored cleanup row로 표시할 수 있지만 effective permission source와 team add catalog에는 포함하지 않는다. Actor cleanup remove는 inactive team row에도 허용한다.
- Team permission의 legacy `none` 또는 operational allow가 아닌 state는 actor resource source/count에 포함하지 않는다. Actor가 직접 회수할 수 있는 user direct legacy `none` row만 inert cleanup row로 표시한다.
- Direct permission revoke는 row deletion이다. Restore는 삭제된 row나 AuditLog를 자동 부활시키지 않고 manager가 `viewer`, `operator`, `builder`, `manager` 중 하나를 선택해 새 grant/upsert를 수행한다. Actor access action은 `none` grant를 받지 않고 revoke를 사용한다. 기존 resource-specific endpoint와 legacy `none` row의 호환성은 별도이며, actor read model은 legacy `none` row를 inert stored row로 표시해 cleanup할 수 있게 한다.
- App creation direct capability도 revoke는 row deletion, restore는 새 grant로 처리한다. Organization manager는 direct row 없이 자동 허용된다.
- Permission tombstone/history table은 도입하지 않는다.

### Organization manager override

Globally active user가 active membership이면서 organization role이 manager인 경우에만 target은 scope 안 operational resource에 automatic manager override를 가진다. 따라서 해당 target의 direct/team/App creation row만 변경해도 effective access가 낮아지지 않는다. Globally deactivated 또는 suspended target은 stored role이 manager여도 override가 active하지 않다.

Actor management API는 active manager-override target의 state-changing resource/capability mutation을 conflict로 거부한다. 이미 desired state인 retry는 Action Contract의 no-op 우선순위를 따른다. 실제 변경이 필요하면 먼저 target을 `member`로 강등한 뒤 별도 action으로 permission을 변경한다. 자기 자신의 정지/강등과 마지막 active manager의 정지/강등은 기존 policy와 row-lock 보호를 유지한다.

Last active manager count는 `User.deactivated_at IS NULL` + active membership + manager role을 모두 만족하는 row만 포함한다. Deactivated manager-role membership을 active manager로 세어 마지막 실제 manager를 제거할 수 있게 해서는 안 된다.

Access profile은 하나의 global `controllable` 값이 아니라 action별 allow/block result를 반환한다. Role set은 `member`와 `manager` desired value별 result를 따로 반환한다. Self, last active manager, manager override, membership/global user state에 따라 강등과 승격의 허용 여부가 다르기 때문이다.

Actor drawer action은 snapshot 기반 lost update를 막기 위해 expected membership/role/auth-state 또는 target row id/absence precondition을 포함한다. Repository는 lock 이후 precondition을 확인하고 현재 상태가 다르면 stale conflict로 거부한다. Cross-organization 또는 missing row 존재를 노출할 수 있는 경우에는 404 hiding을 우선한다.

## Action Contract

Actor management mutation은 한 요청에서 한 항목만 변경한다. 하나의 discriminated command endpoint가 다음 action을 구분한다.

- membership suspend/reactivate
- organization role set
- team membership add/remove
- user direct resource permission grant/revoke
- App creation permission grant/revoke

여러 action을 한 번에 적용하는 bulk request는 만들지 않는다. 각 action은 별도 confirm, transaction, canonical audit event를 가진다. Optional reason은 JSON body로 받고 blank를 null로 정규화하며 최대 길이와 control-character 제한을 적용한다.

모든 action은 profile에서 받은 membership id/state/role과 global user active snapshot을 optimistic precondition으로 포함한다. Team/direct/App-creation action은 대상 row id와 auth_state 또는 명시적 absence precondition도 포함한다.

Scope와 authority를 검증하고 lock을 얻은 뒤 다음 우선순위를 적용한다.

1. `expected_membership_id`와 `expected_user_active`가 다르면 no-op보다 먼저 `409 stale_state`로 거부한다.
2. Existing row를 대상으로 하는 update/revoke의 expected row id가 현재 row id와 다르면 desired value가 같아도 ABA로 보고 `409 stale_state`로 거부한다.
3. 같은 membership row의 suspend/reactivate/role desired value, existing team add, missing team remove, same-row direct update의 desired auth state, same-value direct create retry, existing App creation grant는 `unchanged`로 끝내고 audit을 만들지 않는다.
4. 그 밖의 membership state/role, source auth state, absence precondition 불일치는 `409 stale_state`로 거부한다.

기존 revoke 계약을 보존해 missing direct/App-creation row는 404로 숨긴다. Cross-organization 또는 hidden row와 stale row를 구분하는 과정에서 존재 노출이 생기면 404 hiding을 우선한다.

Version column을 추가하지 않으므로 같은 row의 값이 바뀌었다가 현재 snapshot/desired value로 돌아온 value-only ABA는 current-state semantics로 수용한다. Row identity가 바뀐 ABA는 expected row id로 차단하고, 중간 변경 이력은 canonical audit이 보존한다.

Globally deactivated target은 cleanup-only다. Active membership suspend, manager-to-member demotion, team remove, direct permission revoke, App creation revoke는 허용한다. Membership reactivate, member-to-manager promotion, team add, direct grant, App creation grant처럼 실제 state를 늘리는 요청은 `target_user_inactive`로 block한다. 위 우선순위에서 이미 `unchanged`로 판정된 요청은 privilege increase가 아니므로 policy block을 만들지 않는다.

Reason은 CRLF/CR을 LF로 정규화하고 양끝 공백을 제거한 뒤 blank를 null로 바꾸며, 정규화 후 Unicode code point 500자 이하로 제한한다. Tab과 LF를 제외한 C0/C1 control 및 bidi override/isolate control은 거부한다. Durable audit에 넣기 전 organization 설정으로 약화할 수 없는 shared fail-closed redaction baseline으로 secret/PII pattern을 치환하고, sanitization 실패 시 raw reason을 저장하지 않은 채 mutation 전체를 실패시킨다.

## Transaction And Audit

Access mutation use case가 transaction owner다.

1. actor organization manager 검증
2. target membership 및 변경 대상 row scope 검증과 필요한 lock
3. Membership/global-user와 existing source row identity precondition 검증. Scope 안 mismatch는 `policy.block` 기록 후 stale result
4. Contract-defined set/add 및 team-remove no-op이면 `unchanged`로 종료. Missing direct/App revoke는 404 유지
5. 나머지 expected snapshot/source value precondition 검증. Scope 안 mismatch는 `policy.block` 기록 후 stale result
6. self/last-manager/manager-override/member-state/target-user-inactive policy 평가. Block이면 `policy.block` 기록 후 typed result
7. before snapshot 생성
8. Listener-tracked object는 repository adapter가 dirty/add/delete 상태로 만들기 전에 해당 object/operation의 manual audit ownership을 등록하고 mutation 적용
9. canonical AuditLog row 추가
10. mutation과 AuditLog를 같은 flush 경계에서 검증
11. commit

Repository와 audit recorder adapter는 commit/rollback을 호출하지 않는다. Audit row 추가 또는 flush가 실패하면 mutation도 rollback한다. 이 흐름에서는 commit 후 best-effort async audit을 성공 조건으로 사용하지 않는다.

Last-manager invariant를 낮출 수 있는 suspend/demote action은 target row를 따로 먼저 잠그지 않는다. 먼저 organization의 active manager membership 후보를 membership id 순으로 `FOR UPDATE`하고, 이어서 그 membership에 대응하는 User row를 같은 membership 순서로 잠근 뒤 `User.deactivated_at IS NULL`인 manager 수를 다시 계산한다. 다른 action은 target membership, target User, resource/team, child permission row 순서로 잠근다. Target membership lock은 absent child-key create도 user 단위로 직렬화하며 DB unique constraint를 최종 방어로 유지한다. 같은 table을 변경하는 기존 경로는 동일 application mutation coordinator/lock protocol에 위임하거나 동등한 compatibility adapter를 사용해야 한다. 이 범위에는 member PATCH/DELETE cleanup, team membership add/remove, user-direct permission grant/revoke, permission-request approval의 App creation grant, App creation permission revoke가 포함된다. 단 기존 route의 organization-manager-or-resource-manage authorization, response/status, latent row/team affiliation 관리 정책은 보존한다. Actor action의 manager-only authorization과 manager-override no-effect 차단을 legacy route에 강제로 적용하지 않는다.

User row lock은 actor action의 linearization 시점에서 global active eligibility를 안정화한다. 이 기능은 이후 별도 global account deactivation 자체를 금지하거나 마지막 manager invariant로 관리하지 않는다. 향후 global user lifecycle mutation을 제공하면 그 경계가 동일 lock order와 organization별 manager 보존 정책을 별도로 채택해야 한다.

TeamMembership처럼 ORM listener 추적 대상인 mutation을 transaction-bound manual recorder가 소유하면 repository adapter가 object를 dirty/add/delete 상태로 만들기 전에 해당 model/object/operation만 suppression registry에 등록해 listener 후보 생성을 막는다. Ownership 등록부터 mutation과 UoW flush 사이에는 query/autoflush를 수행하지 않는다. Session 전체 listener를 비활성화하지 않으며 unrelated tracked mutation audit은 유지한다. Commit/rollback/soft-rollback에서 suppression state를 반드시 정리한다.

Applied mutation의 canonical action과 category는 existing naming을 재사용한다. Membership/role은 `organization.member.update` + `action`, team membership은 `team_membership.created/deleted` + `data_change`, user direct permission은 `user_*_permission.created/updated/deleted` + `data_change`, App creation permission은 `user_app_creation_permission.created/deleted` + `action`을 사용한다. Actor management 진입점을 이유로 같은 mutation에 aggregate audit event를 추가하지 않는다.

Low-level mutation adapter는 AuditLog를 임의로 commit하지 않고 pure mutation descriptor를 반환한다. Actor action use case는 위 row-level audit을 기록한다. Legacy member removal처럼 기존 aggregate `permission.revoke` 계약이 있는 caller는 같은 descriptor/lock primitive를 사용하되 기존 aggregate audit을 유지한다. Route별 audit 의미를 `audit_mode` boolean 같은 repository flag로 섞지 않고 caller use case가 소유한다.

Manual recorder가 저장하는 `before`/`after`도 target별 safe-field allowlist에서 직접 구성한다. ORM object 전체, model dump, relationship, arbitrary metadata를 먼저 저장한 뒤 응답에서만 가리는 방식은 허용하지 않는다. Audit detail read model은 같은 allowlist와 organization provenance를 다시 적용한다.

Scope가 확인된 뒤 self-control, last-manager, manager override, member-state, target-user-inactive, stale precondition policy에 막힌 요청은 mutation action을 위조하지 않고 `policy.block` + `category=action` + `status=failure`로 기록한다. Policy block의 canonical target은 scoped `organization_membership`과 membership id다. Metadata는 `target_user_id`, `requested_action`, machine `policy_reason`, sanitized optional `reason`과, scope를 확인한 경우에만 optional resource/team opaque id를 허용한다. Target name/email, raw request, expected/current snapshot 전체는 저장하지 않는다. Block audit을 같은 UoW에서 commit한 뒤 inbound adapter가 400/409로 mapping하며, audit commit이 실패하면 raw fallback 없이 500으로 실패하고 mutation은 없다. Scope 안 caller 권한 부족은 기존 `permission.denied`를 사용하되 target scope 검증 전에는 organization reference 외 target-aware metadata를 남기지 않는다. 401, validation 422, resource-hidden 404, desired-state no-op은 actor access audit을 만들지 않는다.

`policy_reason`은 `access_management.self_control_forbidden`, `access_management.last_active_manager`, `access_management.manager_override_active`, `access_management.member_state_not_manageable`, `access_management.target_user_inactive`, `access_management.stale_state` allowlist만 사용한다. Exception text나 raw expected/current state를 reason으로 저장하지 않는다.

## Safe Audit Detail

Audit detail API는 DB의 generic `before`/`after`를 그대로 반환하지 않는다. Supported target/action별 allowlist로 optional change summary를 만든다.

허용 action/target 조합과 field allowlist는 다음과 같다. Target type만 일치하고 action이 다르면 summary를 만들지 않는다.

- `organization.member.update` + organization membership: `organization_id`, `user_id`, `membership_state`, `organization_auth_state`
- `team_membership.created/deleted` + team membership: `grantee_organization_id`, `team_id`, `user_id`
- `user_*_permission.created/updated/deleted` + 해당 user resource permission: `grantee_organization_id`, `user_id`, target resource id와 `auth_state`
- `user_app_creation_permission.created/deleted` + App creation permission: `user_id`, `grantee_organization_id`

Raw payload, secret, credential value, hidden resource reference, allowlist 밖 column은 제외한다. Unknown target/action의 change summary는 반환하지 않는다.

Team membership add/remove audit metadata는 mutation transaction에서 관찰한 advisory `affected_resource_source_count`를 포함할 수 있다. 이는 resource id 목록이나 effective access loss/gain 수가 아니며 audit detail도 같은 의미로 표시한다.

Manual recorder는 update에도 changed field만이 아니라 target별 complete safe snapshot을 `before`/`after`에 기록해 organization provenance를 보존한다. Organization-bearing target은 snapshot의 `organization_id` 또는 `grantee_organization_id`가 request organization과 일치할 때만 summary를 반환한다. Create는 `after`, delete는 `before`, update는 양쪽 snapshot에서 provenance가 확인되어야 한다. Historical/deleted target의 same-organization opaque UUID는 표시할 수 있지만 current resource name/path를 재해석하지 않는다. Organization provenance가 없거나 불일치하면 summary 전체를 생략한다.

`policy.block`은 row 변경이 아니므로 `change_summary`를 반환하지 않는다. Audit detail은 safe `requested_action`과 `policy_reason`으로 차단 이유를 설명한다.

## Architecture Scope

MBA-188은 실제 actor access use case에 필요한 package와 adapter만 만든다. 빈 audit domain scaffold나 repository 전체 audit migration roadmap을 이 변경에 포함하지 않는다. 이 구현은 access mutation과 canonical AuditLog를 같은 DB transaction에 기록한다. Durable outbox 일반화는 MBA-189 범위이며 MBA-188의 대체 구현 선택지가 아니다.

ADR-0022가 첫 실제 pilot으로 고정한 deployment preflight use case/port/adapter 이관을 먼저 완료해야 한다. MBA-183은 package scaffold까지만 반영했으므로 Linear MBA-191에서 reference implementation을 완성하고, MBA-188의 `access_management` code implementation은 그 완료 뒤 시작한다. Pilot 순서를 변경하려면 별도 ADR로 ADR-0022를 대체한다.

전체 audit producer inventory, sync/async/outbox 일반화, retry/dead-letter, read repository 이관은 별도 후속 작업으로 다룬다. 이 결정은 deployment preflight pilot 다음의 permission mutation 적용 범위를 actor access-management로 제한하는 기준이다.

## Consequences

장점:

- Audit reader가 organization mutation 권한을 얻는 privilege escalation을 막는다.
- Suspension과 permission revoke/restore 의미가 분리된다.
- Team/direct/manager permission source를 actor 관점에서 설명할 수 있다.
- 보안 mutation과 audit의 원자성을 검증할 수 있다.
- 기존 resource-specific endpoint와 UI를 점진적으로 같은 use case에 위임할 수 있다.

비용:

- Actor-centric read model과 application port/adapter가 추가된다.
- Legacy mutation endpoint와 신규 access action endpoint가 과도기 동안 공존한다.
- Manager role을 유지한 채 latent permission row를 정리하는 actor UI는 제공하지 않는다.
- Restore가 과거 state 자동 복원이 아니므로 manager가 auth_state를 다시 선택해야 한다.

## Follow-up

- Linear MBA-191에서 ADR-0022의 deployment preflight reference pilot을 먼저 완료한다.
- MBA-188에서 access-management use case, transaction-bound audit recorder, safe audit detail read model을 구현한다.
- 기존 member/user direct/App creation endpoint를 동일 use case로 점진 위임한다.
- 전체 audit 기록·조회 경계 이관은 Linear MBA-189에서 수행한다.
- Bulk access operation이나 historical restore 요구가 확인되면 별도 ADR과 data model을 검토한다.
