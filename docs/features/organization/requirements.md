# Organization Requirements

Status: Draft
Related Features: auth, workflow, knowledge, llm-credentials, audit-tracing, admin-dashboard, security-alert

## Purpose

Organization 기능은 Nodease의 tenant-like 작업 경계와 RBAC 운영 기반을 담당한다. 현재 구현 범위는 기본 organization foundation 생성, active organization context 조회/선택, organization 이름/options 수정, organization membership 초대/수락/거절/상태 변경/제거, team 생성/수정/멤버 배정/비활성화, workflow, Knowledge Base, LLM credential에 대한 team/user direct permission 조회/부여/회수, App 생성 권한 검사와 권한 신청 제출이다.

Auth는 사용자를 인증하고 signup/Google OAuth 성공 시 기본 organization foundation 생성을 호출한다. Organization은 생성된 organization, membership, team, resource permission의 scope와 운영 변경을 소유한다. Workflow, Knowledge, LLM credential, Audit feature의 리소스별 동작 의미는 각 feature 문서가 소유하며, Organization 문서는 공통 scope/RBAC 경계와 현재 구현된 권한 관리 API만 정의한다.

권한 모델(`auth_state`, 판정 순서, team template 목표 모델)의 기준은 [data_model.md](../../data_model.md)의 RBAC 요약과 [ADR-0006](../../decisions/ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md)이다. 이 문서는 공통 용어와 matrix를 재정의하지 않고 organization feature가 책임지는 적용 지점만 명시한다.

## User Stories

- 신규 사용자는 signup 또는 Google OAuth 성공 후 기본 organization, manager membership, 기본 team을 가진 상태로 서비스를 시작할 수 있다.
- 사용자는 접근 가능한 active organization 목록을 보고 현재 작업할 organization을 선택할 수 있다.
- organization manager는 organization 이름과 options를 수정할 수 있다.
- organization manager는 가입된 user id를 기준으로 멤버를 초대하고, 멤버 상태와 organization 권한을 변경하거나 제거할 수 있다.
- 초대받은 사용자는 Sidebar 사용자 메뉴의 알림 overlay에서 본인의 organization 초대를 수락하거나 거절할 수 있다.
- organization manager는 team을 만들고 수정하며, active organization member를 team에 배정하거나 team에서 제거할 수 있다.
- organization manager는 더 이상 운영에 쓰지 않는 team을 비활성화할 수 있다.
- organization manager 또는 대상 resource manager는 workflow/Knowledge Base/LLM credential 권한을 team 또는 user direct permission으로 부여하거나 회수할 수 있다.
- 멤버는 organization scope 안에서 부여된 resource 권한만 사용할 수 있고, scope 밖 resource는 존재 여부를 알 수 없어야 한다.

## Functional Requirements

- ORG-REQ-001: Auth signup 또는 Google OAuth 신규 사용자 생성 성공 시 시스템은 기본 organization, active manager membership, `Default` team, 기본 team membership을 준비해야 한다.
- ORG-REQ-002: 시스템은 standalone organization 생성 API를 제공하지 않는다. 기본 organization 생성은 auth 가입 경로에서 호출되는 foundation 생성 책임으로 제한한다.
- ORG-REQ-003: `GET /organizations`는 현재 사용자가 active membership으로 접근 가능한 active organization만 반환해야 한다.
- ORG-REQ-004: `GET /organizations/memberships`는 현재 사용자의 active 또는 invited organization membership 요약을 반환해야 한다.
- ORG-REQ-005: 조직 scope API는 `X-Organization-Id` header로 active organization을 결정해야 하며, 서버는 active organization을 session/cookie에 저장하지 않아야 한다 ([ADR-0009](../../decisions/ADR-0009-active-organization-header-context.md)).
- ORG-REQ-006: `GET /organizations/current`는 `X-Organization-Id`가 active organization membership scope 안에 있을 때 현재 organization 상세를 반환해야 한다.
- ORG-REQ-007: `GET /organizations/{organization_id}`는 현재 사용자가 접근 가능한 active organization 상세만 반환해야 한다.
- ORG-REQ-008: `PATCH /organizations/{organization_id}`는 path organization과 `X-Organization-Id`가 일치하고 현재 사용자가 organization manager일 때 `name` 또는 `options`를 수정해야 한다.
- ORG-REQ-009: organization 수정은 빈 PATCH body, blank name, null options를 거부해야 한다.
- ORG-REQ-010: organization 수정 성공은 `organization.update` audit을 기록해야 한다.
- ORG-REQ-011: organization member 목록 조회는 organization manager만 수행할 수 있어야 한다.
- ORG-REQ-012: member 목록 조회는 기본적으로 active, invited, suspended membership을 반환하고, `state=removed`가 지정되면 removed membership을 조회할 수 있어야 한다.
- ORG-REQ-012A: member 목록은 각 member의 이번 달(KST) LLM 비용 묶음(`total_cost`, `workflow_execution_cost`, `agent_builder_cost`)을 함께 반환해야 한다. 비용은 member의 현재 membership state가 아니라 기간 안에 해당 `user_id`로 기록된 eligible usage를 기준으로 합산한다. 따라서 invited, suspended, removed member도 해당 달 사용 기록이 있으면 실제 비용을 반환하고, 사용 기록이 없을 때만 모든 비용을 0으로 반환한다. eligible usage, legacy `organization_id IS NULL`, 명시적 타 organization usage 제외, Agent Builder 성공 행과 비용 구분 규칙은 admin-dashboard FR-012와 동일하다.
- ORG-REQ-013: organization manager는 active user id와 `organization_auth_state`(`member` 또는 `manager`)로 멤버를 초대할 수 있어야 한다.
- ORG-REQ-014: 멤버 초대는 자기 자신 초대를 거부해야 한다.
- ORG-REQ-015: 이미 active 또는 invited인 멤버를 다시 초대하면 기존 membership을 반환해야 한다.
- ORG-REQ-016: suspended 멤버 재초대는 거부하고, removed 멤버 재초대는 기존 membership을 invited 상태로 되살려야 한다.
- ORG-REQ-017: 멤버 초대 성공은 `organization.invite` audit을 기록해야 한다.
- ORG-REQ-018: 초대 수락은 초대받은 본인만 수행할 수 있으며 manager 권한을 요구하지 않아야 한다.
- ORG-REQ-019: active 초대를 다시 수락하면 현재 membership을 반환해야 하고, suspended/removed 초대 수락은 거부해야 한다.
- ORG-REQ-020: 초대 수락 성공은 `organization.member.accept` audit을 기록해야 한다.
- ORG-REQ-019A: 초대 거절은 초대받은 본인만 수행할 수 있으며 manager 권한을 요구하지 않아야 한다. 거절 성공은 membership을 `removed`로 전환하고 `organization.member.decline` audit을 기록해야 한다.
- ORG-REQ-021: organization manager는 active member를 suspended로 변경하거나 suspended member를 active로 되돌릴 수 있어야 한다.
- ORG-REQ-022: organization manager는 active member의 `organization_auth_state`를 `member` 또는 `manager`로 변경할 수 있어야 한다.
- ORG-REQ-023: member update는 invited member의 강제 active/suspended 전환, removed member update, 자기 자신의 상태/권한 변경, 마지막 active manager 제거/강등을 거부해야 한다.
- ORG-REQ-024: member update 성공은 `organization.member.update` audit을 기록해야 하며, no-op update는 audit을 기록하지 않아야 한다.
- ORG-REQ-025: organization manager는 자기 자신과 마지막 active manager를 제외한 멤버를 removed 상태로 변경할 수 있어야 한다.
- ORG-REQ-026: 멤버 제거는 해당 user의 team membership, user workflow/Knowledge Base/LLM credential direct permission, user App 생성 권한 row를 함께 정리해야 한다.
- ORG-REQ-027: 멤버 제거 성공은 `organization.member.remove` audit과 cleanup aggregate `permission.revoke` audit을 기록해야 한다.
- ORG-REQ-028: 이미 removed인 멤버 제거 요청은 idempotent success로 처리해야 한다.
- ORG-REQ-029: team 목록/생성/수정/멤버 조회/멤버 추가/멤버 제거/비활성화는 organization manager만 수행할 수 있어야 한다 ([ADR-0011](../../decisions/ADR-0011-team-router-rbac-service-boundary.md)).
- ORG-REQ-030: team 목록 조회는 active/inactive team을 함께 반환하고, `limit`은 1 이상 100 이하로 제한해야 한다.
- ORG-REQ-031: team 생성은 active organization scope 안에서 unique team name, optional description, `is_auto_add` 값을 저장해야 한다.
- ORG-REQ-032: team 수정은 active team에 대해서만 name, description, managed_by, is_auto_add를 변경할 수 있어야 한다.
- ORG-REQ-033: team `managed_by`는 비활성 user나 organization scope 밖 user를 거부해야 한다.
- ORG-REQ-034: team member 추가는 active team과 active organization membership을 가진 user만 허용해야 한다.
- ORG-REQ-035: 기존 team membership 추가와 없는 team membership 제거는 idempotent하게 처리해야 한다.
- ORG-REQ-036: team 비활성화는 team을 삭제하지 않고 `is_active=false`, `deactivated_at` 설정으로 처리해야 하며, 이미 inactive인 team에는 idempotent success를 반환해야 한다.
- ORG-REQ-037: resource permission 관리 API는 workflow, Knowledge Base, LLM credential에 대한 team permission 및 user direct permission 조회/부여/회수를 제공해야 한다.
- ORG-REQ-038: permission 조회/변경은 organization manager 또는 대상 workflow/KB/LLM credential의 `manage` 권한 보유자만 수행할 수 있어야 한다.
- ORG-REQ-039: permission 부여는 active team 또는 active organization member user만 grantee로 허용해야 한다.
- ORG-REQ-040: permission 부여 요청은 canonical resource `auth_state`만 허용해야 한다. legacy 값(`read/write/execute/admin`)은 기존 row 해석에만 사용하고 신규 요청에서는 거부해야 한다.
- ORG-REQ-041: permission upsert/delete는 row 단위 data-change audit(`team_workflow_permission.created`, `team_workflow_permission.updated`, `team_workflow_permission.deleted`, `team_knowledge_permission.*`, `team_llm_permission.*`, `user_workflow_permission.*`, `user_knowledge_permission.*`, `user_llm_permission.*`)을 기록해야 한다. KB permission API는 grant/revoke mutation과 같은 DB transaction에서 audit row를 기록해야 하며, Core upsert/bulk delete 경로도 audit을 정확히 한 번 남겨야 한다.
- ORG-REQ-042: organization scope 밖 resource는 `404 resource.not_found`로 숨기고, scope 안 권한 부족은 `403 permission.denied`로 응답해야 한다 ([ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)).
- ORG-REQ-043: 클라이언트는 active organization id를 localStorage의 `moduly_active_organization_id`에 저장하고, `apiClient` 요청에 `X-Organization-Id` header를 자동 첨부해야 한다.
- ORG-REQ-044: dashboard layout은 active organization을 확인하고, 하나뿐이면 자동 선택하며, 여러 개면 사용자가 선택하도록 해야 한다.
- ORG-REQ-045: dashboard sidebar는 현재 organization 이름과 manager 여부를 조회하고, manager가 아닌 사용자에게 관리 메뉴를 숨겨야 한다.
- ORG-REQ-046: admin console은 manager에게 멤버/팀/workflow permission/KB permission/LLM credential permission 관리 UI를 제공하고, manager가 아닌 사용자에게 관리 권한 없음 상태를 표시해야 한다.
- ORG-REQ-047: App 생성(`POST /apps`, "새 워크플로우")은 organization owner/manager 또는 `user_app_creation_permissions` row 보유자만 수행할 수 있어야 한다 ([ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md)).
- ORG-REQ-048: App 생성 권한이 없는 사용자의 App 생성 요청은 `403 permission.denied`로 차단하고, 클라이언트는 이 응답에서 권한 신청 UI로 연결해야 한다.
- ORG-REQ-049: App 생성 권한 신청은 `permission_requests`에 요청 권한 `app.create`와 신청 사유를 저장해야 한다.
- ORG-REQ-050: 같은 조직에 pending 신청이 있거나 이미 App 생성 권한을 보유한 사용자(`user_app_creation_permissions` row 보유 또는 owner/manager)의 App 생성 권한 신청은 거부해야 한다.
- ORG-REQ-051: 거절된 App 생성 권한 신청자는 재신청할 수 있어야 한다.
- ORG-REQ-052: App 생성 권한 신청 제출/승인/거절은 `permission_request.created/approved/rejected`, 승인에 따른 실제 권한 부여는 `user_app_creation_permission.created` audit으로 기록해야 한다 ([ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md)).
- ORG-REQ-053: App 생성 후 배포 권한은 생성자에게 자동 부여되는 workflow manager permission으로 따라오므로, 별도 배포 권한 신청 항목을 두지 않아야 한다.
- ORG-REQ-054: `GET /notifications`는 현재 로그인한 사용자의 invited organization membership을 `organization.invitation` 알림으로 파생해 반환해야 한다. 이 endpoint는 invitation source만 소유하며 별도 invitation notification table, 읽음 상태, 히스토리를 만들지 않아야 한다.
- ORG-REQ-055: `GET /notifications/stream`은 현재 로그인한 사용자 기준 SSE stream을 제공하고, organization 초대 변경 또는 Security Alert notification projection 변경 후 `notifications.changed` 이벤트를 발행할 수 있어야 한다. Event payload는 source of truth가 아니며 클라이언트는 invitation 목록과 권한이 있는 Security Alert summary를 source별로 재조회해야 한다.
- ORG-REQ-056: Sidebar notification overlay는 organization invitation과 Security Alert를 서로 다른 source/section으로 표시해야 한다. Security Alert section·badge·summary 요청은 현재 active organization owner/manager에게만 제공하고, 일반 member의 invitation 흐름은 그대로 유지해야 한다. 한 source의 조회 실패가 다른 source를 숨기면 안 된다. Security Alert 상세 계약은 [security-alert](../security-alert/requirements.md)이 소유한다.
- ORG-REQ-057: Production resource permission API의 target model과 team/user permission model 선택은 중앙 resource permission registry를 사용해야 한다. Endpoint adapter가 resource별 response/audit mapping을 유지하더라도 registry에 없는 resource/grantee type이 다른 permission table로 fallback해서는 안 된다.
- ORG-REQ-058: organization manager는 current organization member의 membership, organization role, active/inactive team membership, App 생성 권한, workflow/Knowledge Base/LLM credential direct 및 team-inherited permission source를 user 중심으로 조회할 수 있어야 한다. Profile은 team count만 포함하고 team membership/resource source 목록은 각각 paginated endpoint로 제공해야 한다. Role control은 desired `member`/`manager`별 허용 여부를 구분해야 한다.
- ORG-REQ-059: actor access profile과 resource access source 조회는 ADR-0009의 membership-first organization manager 판정을 통과한 caller만 허용해야 한다. Audit `auditor`/`raw_auditor` 권한만으로는 조회하거나 변경할 수 없다 ([ADR-0023](../../decisions/ADR-0023-audit-actor-access-management-boundary.md)).
- ORG-REQ-060: audit actor가 user가 아니거나 actor id가 없거나 current organization에서 active/suspended membership이 없는 invited/removed/historical user이면 audit detail은 유지하되 access-management profile/control을 제공하지 않아야 한다.
- ORG-REQ-061: actor access mutation은 한 요청에서 membership, role, team membership, direct resource permission, App 생성 권한 중 한 항목만 변경해야 하며 action discriminator로 필수 필드를 검증해야 한다.
- ORG-REQ-062: membership suspension은 current organization membership만 `suspended`로 변경하고 team/direct/App-creation permission row를 보존해야 한다. Suspended member의 effective organization access는 active membership gate에서 fail-closed하며, 재활성화 후 보존된 source를 다시 평가해야 한다. Suspended 상태에서는 재활성화와 manager-to-member/team/direct/App cleanup만 허용하고 promotion/add/grant는 `member_state_not_manageable`로 차단해야 한다.
- ORG-REQ-063: direct resource permission 복구는 삭제된 row 또는 AuditLog를 자동 복원하지 않고 manager가 `viewer`, `operator`, `builder`, `manager` 중 하나를 명시해 새 grant/upsert를 수행해야 한다. Actor access grant는 `none`을 거부하고 revoke를 사용한다. Team-inherited permission은 explicit user deny가 아니라 team membership 제거/재추가로 제어해야 한다.
- ORG-REQ-064: active membership과 manager role로 manager override가 적용되는 target은 direct/team/App-creation row 변경만으로 effective access가 낮아지지 않으므로 actor access API에서 state-changing mutation을 conflict로 거부해야 한다. Already-desired retry는 no-op 규칙을 따르고, 실제 변경은 target을 `member`로 강등한 뒤 별도 action으로 수행한다. Suspended target의 stored manager role은 active override로 취급하지 않는다.
- ORG-REQ-065: actor access action은 optional reason을 JSON body로 받아야 한다. CRLF/CR을 LF로 정규화하고 trim한 blank는 null로 바꾸며, 정규화 후 최대 500 Unicode code point로 제한한다. Tab/LF 외 C0/C1 control과 bidi override/isolate control은 거부한다. Durable audit 저장 전 organization 설정으로 약화할 수 없는 shared fail-closed redaction baseline으로 secret/PII pattern을 치환하고 sanitization 실패 시 raw reason과 mutation을 저장하지 않아야 한다.
- ORG-REQ-066: actor access mutation use case는 mutation과 canonical audit row를 같은 DB transaction에 기록해야 한다. Audit 기록 실패 시 보안 mutation을 성공 처리하지 않아야 하며 durable outbox 일반화는 MBA-189 범위로 남겨야 한다.
- ORG-REQ-067: actor access no-op은 state를 변경하거나 audit을 만들지 않아야 한다. Last-manager 후보 action은 active-manager membership을 id 순으로 잠근 뒤 대응 User row를 같은 membership 순서로 잠그고, 다른 action은 target membership, target User, resource/team, child row 순으로 lock해야 한다. Concurrent direct permission/team/App 생성 권한 mutation은 같은 lock protocol과 기존 conflict/not-found 계약으로 한 요청만 적용되어야 하며, 같은 table을 변경하는 legacy endpoint도 동일 mutation coordinator 또는 동등한 compatibility adapter를 사용해야 한다. Legacy route의 authorization, response/status, latent row/team affiliation 관리 정책은 보존하고 actor-only manager-override 차단을 강제하지 않아야 한다.
- ORG-REQ-068: 모든 actor access action은 confirm 시점의 expected global user active state와 membership id/state/role을 포함해야 한다. Team/direct/App action은 target row id/auth-state 또는 absence precondition도 포함한다. Scope/lock 뒤 membership id, user active state, existing source row id mismatch를 먼저 stale로 판정하고, 그 다음 same desired state retry를 no-op으로 처리하며, 나머지 state/auth/absence mismatch를 409로 거부해야 한다. ABA row id mismatch는 desired value가 같아도 stale이며, stale은 mutation/row-level audit 없이 ORG-REQ-070의 `policy.block`만 기록해야 한다.
- ORG-REQ-069: resource access의 `effective_auth_state`는 active membership gate와 manager override까지 적용한 결과여야 한다. Suspended target은 stored source를 별도 표시하되 effective state는 `none`, active manager target은 listed source와 별개로 `manager`여야 한다.
- ORG-REQ-070: applied actor mutation은 row-level canonical action을 정확히 한 번 기록해야 한다. Membership/role과 App creation은 `action`, team membership과 user direct permission은 `data_change` category를 사용해야 한다. Scope가 확인된 self/last-manager/manager-override/member-state/target-user-inactive/stale block은 scoped membership을 target으로 `policy.block` failure audit을 기록하고, caller 권한 부족은 `permission.denied`를 사용해야 한다. Target scope 확인 전에는 target-aware metadata를 남기지 않으며 401, 422, hidden 404, desired-state no-op은 actor access audit을 만들지 않아야 한다.
- ORG-REQ-071: globally deactivated target은 active/suspended membership이 있으면 cleanup profile을 반환하되 effective access와 manager override는 disabled여야 한다. Suspend, manager-to-member demotion, team remove, direct/App revoke만 허용하고 reactivate, promotion, team add, direct/App grant는 `target_user_inactive`로 block해야 한다.
- ORG-REQ-072: 모든 actor action은 `expected_user_active`를 포함하고 target User row를 lock해야 한다. Lock 시점 이전에 global active state가 달라졌으면 no-op보다 먼저 stale로 차단하고, actor action transaction 이후의 별도 global account lifecycle 변경까지 이 기능이 금지한다고 간주해서는 안 된다.
- ORG-REQ-073: last active manager count는 globally active User + active membership + manager role을 모두 만족하는 row만 포함해야 한다. Active manager membership을 id 순으로 먼저 잠근 뒤 대응 User row를 같은 membership 순서로 잠그고 count를 다시 계산해야 한다. 향후 global account lifecycle mutation이 last-manager invariant도 보장해야 한다면 해당 경계가 같은 lock protocol과 별도 정책을 채택해야 한다.
- ORG-REQ-074: access-management application은 actor query, membership/global-user lock, team membership, user-direct resource permission, App creation permission, resource scope/catalog를 capability별 port로 분리해야 한다. Concrete SQLAlchemy adapter 하나가 여러 port를 구현할 수 있지만 application use case가 모든 저장 책임을 가진 단일 god-repository protocol에 의존해서는 안 된다. ORM object/listener suppression token은 adapter 안에 남기고 application/audit port에는 framework-independent mutation descriptor만 전달해야 한다.
- ORG-REQ-075 (Target Runtime Contract): Organization authorization adapter는 authenticated user와 anonymous public audience를 공통으로 표현하는 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 반환해야 한다. Public audience에 synthetic subject ID/revision을 만들지 않아야 한다.
- ORG-REQ-076 (Target Runtime Contract): User active state, organization lifecycle, membership 생성/상태/role, team membership과 relevant direct/team permission처럼 authorization 결과에 영향을 주는 변경은 decision revision을 바꿔야 한다. Stale revision은 current allow 근거로 재사용할 수 없어야 한다.
- ORG-REQ-077 (Target Runtime Contract): Authorization decision revision은 client가 제공하거나 Memory가 조합하는 값이 아니라 source-owning adapter가 발급하는 opaque value여야 한다. Email, name, raw permission row, team 목록과 secret을 포함하지 않아야 한다.
- ORG-REQ-078 (Target Runtime Contract): Conversation Access Grant, credential principal과 billing principal은 organization membership을 증명하지 않는다. Authenticated internal Chatbot은 current user의 active membership과 별도 access permission을 통과해야 하며 public route는 anonymous audience로 평가해야 한다.
- ORG-REQ-079: Resource permission bulk grant는 하나의 resource type, 하나의 grantee type, unique resource/grantee ID 목록과 canonical `viewer|operator|builder|manager`를 받아 Cartesian product 전체에 적용해야 한다. 한 요청은 최대 50개 resource-grantee pair로 제한하고, 모든 resource scope/manage, active grantee, lifecycle과 Knowledge self-escalation 규칙을 mutation 전에 재검증해야 한다. Permission row와 row별 canonical audit은 하나의 transaction에서 전체 commit하거나 한 target이라도 실패하면 전체 rollback해야 한다.
- ORG-REQ-080 (Target Privacy Bootstrap): MBA-362가 ADR-0070 enforcement를 도입하면 신규 사용자 기본 organization foundation 생성 Unit of Work는 current platform privacy contract/validity revision을 참조하고 provider revision이 null인 server-owned initial `baseline_only` policy revision과 positive initial Organization validity revision/epoch을 organization, manager membership, Default team과 함께 원자적으로 생성해야 한다. Review management/audit readiness가 없는 bootstrap policy는 `manual_review` action을 만들 수 없어야 한다. Policy 또는 validity provision 실패는 foundation 전체를 rollback하고, 이미 active foundation이 있는 idempotent retry는 기존 organization과 exact current policy/Organization validity를 반환해야 하며 missing policy/validity를 runtime implicit default로 보충해서는 안 된다. Public signup/OAuth request와 organization response는 provider/mode/revision/validity scope 또는 epoch override field를 받거나 노출하지 않는다.
- ORG-REQ-081 (Target Privacy Rollout): Privacy enforcement 활성화 전 signup, OAuth, seed/admin을 포함한 모든 Organization creation path가 ORG-REQ-080의 dual-write-capable writer generation으로 수렴해야 한다. 구버전 writer를 drain/fence한 뒤 existing Organization backfill을 idempotent하게 수행하고 bounded rescan의 missing policy/validity가 0이며 writer-generation readiness marker가 current임을 확인해야 한다. Activation coordination transaction은 migration 중 nullable이던 `privacy_foundation_writer_generation`과 initial policy/current Organization validity ref를 no-default non-null DB commit constraint로 전환한 뒤에만 enforcement를 활성화한다. New writer는 marker/refs를 명시적으로 쓰고 이를 모르는 old writer insert는 Organization row 없이 rollback해야 한다. Concurrent new-writer create와 backfill은 Organization row/uniqueness로 직렬화하고, 이 순서를 보장할 coordinated rollout이 없으면 maintenance window를 사용한다. Enforcement 뒤 dual-write를 모르는 구버전 writer image의 startup/rollback은 deployment readiness에서 거부하며 DB constraint가 최종 fence다. Exact FK/deferred-constraint shape는 MBA-362 migration에서 확정하되 server default로 old writer를 통과시키면 안 된다.

## Policies And Edge Cases

- Organization scope 판정과 resource permission 판정 순서는 [data_model.md](../../data_model.md)의 RBAC 요약을 따른다. active membership row가 우선이며, invited/suspended/removed membership은 fail-closed다.
- Runtime authorization result는 decision revision으로 snapshot freshness를 표현하되 revision 자체가 permission을 부여하지 않는다. Consumer는 current decision과 resource/source policy revision을 함께 검증한다.
- membership row가 없는 legacy organization `created_by` 또는 `managed_by` user만 manager fallback을 받는다.
- Organization membership 권한은 `member`와 `manager`만 사용한다. Resource permission `auth_state`(`none/viewer/operator/builder/manager`, audit용 `auditor/raw_auditor`)와 혼동하지 않는다.
- user direct permission은 additive allow 전용이다. team 권한을 낮추지 못하고 explicit deny는 없다.
- Actor-centric resource access에는 direct source, team source, effective auth_state를 구분해 표시한다. Organization manager override가 active인 target은 개별 resource revoke control을 제공하지 않는다 ([ADR-0023](../../decisions/ADR-0023-audit-actor-access-management-boundary.md)).
- Suspended member의 stored permission row는 관리 profile에서 확인할 수 있지만 role promotion, 신규 direct grant, team add, App-creation grant는 active로 재활성화한 뒤 수행한다. Manager-to-member 강등, existing direct/App-creation revoke와 team remove는 suspended 상태에서도 cleanup 목적으로 허용한다.
- Inactive team membership은 profile에 cleanup 대상으로 표시할 수 있지만 effective team permission과 team add catalog에는 포함하지 않는다.
- Globally deactivated target도 stored source를 표시하되 cleanup-only action만 제공한다. Global account reactivation 자체는 이 feature 범위가 아니다.
- Actor resource source projection은 legacy workflow/LLM user-direct `none` row를 inert stored row로 표시할 수 있지만 effective access와 allow count에는 합산하지 않는다. Team permission의 `none` 또는 operational allow가 아닌 state는 actor source/projection/count에서 제외하고 resource-centric permission UI에서 관리한다. 신규 actor action은 `none` row를 만들지 않는다.
- Actor access action은 bulk request가 아니다. 각 action은 별도 confirm, transaction, audit event를 가지며 client는 성공 또는 conflict 이후 server profile을 재조회한다.
- Policy block audit metadata는 safe `requested_action`, machine `policy_reason`, sanitized optional `reason`만 사용하고 target name/email, raw request body, expected snapshot 전체를 복사하지 않는다.
- 멤버 제거 시 해당 user의 permission cleanup은 aggregate audit(`permission.revoke` + `reason='organization.member.remove'`)으로 기록한다. cleanup 대상에는 `user_app_creation_permissions`도 포함한다 ([ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md) — 승인과 멤버 제거가 경합해도 최종 상태가 정리되는 안전망).
- App 생성 권한의 개별 회수(보유 목록 조회, row 삭제, `user_app_creation_permission.deleted` audit)는 [admin-dashboard](../admin-dashboard/requirements.md) feature(FR-014 회수 확장)가 소유한다. 회수된 사용자는 보유 권한과 pending 신청이 없는 상태로 돌아가므로 ORG-REQ-050 조건에 따라 재신청할 수 있다.
- 이미 inactive인 팀의 비활성화 요청은 같은 조직 manager라면 idempotent success로 처리한다 ([ADR-0011](../../decisions/ADR-0011-team-router-rbac-service-boundary.md)).
- permission grant 요청은 canonical auth_state 값만 받는다. legacy 값(`read/write/execute/admin`)은 기존 row 해석에만 사용하고 신규 요청에서는 거부한다.
- 서버는 active organization을 session/cookie에 저장하지 않는다. header가 없는 legacy 경로만 제한적 primary organization fallback을 사용한다.
- App 생성 권한 검사 도입 시 기존 member에 대한 backfill 마이그레이션은 하지 않는다 (실서비스 데이터 없음, [ADR-0016](../../decisions/ADR-0016-permission-request-and-app-creation-permission.md)). 데모/개발 환경은 seed가 계정별 권한을 구성한다 — 관리자는 owner/manager로 자동 허용, 기존 author 계정은 `user_app_creation_permissions` row 보유, 신입 계정은 row 없음.
- App 생성 차단은 [ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)에 따라 `403 permission.denied` + audit으로 기록하고, 클라이언트는 이 응답에서 권한 신청 UI로 연결한다.
- 이미 처리된(승인/거절) 권한 신청의 중복 처리 요청은 거부한다. pending 신청의 동시 승인/거절 경합이 중복 부여로 이어지지 않아야 한다.
- MBA-176 이후 user direct resource permission API와 cleanup은 workflow, Knowledge Base, LLM credential을 포함한다. user audit permission은 현재 구현 범위가 아니다. 조직 수준 App 생성 권한 row도 멤버 제거 cleanup 대상이다.
- Workflow user direct permission grant/revoke는 중앙 Access Management와 legacy API 모두 access subject를 먼저 잠그고, 해당 Workflow의 App lifecycle lock과 Workflow permission scope, permission key 순으로 획득한다. Team direct permission은 subject 단계 없이 App lifecycle lock을 Workflow permission scope보다 먼저 획득한다. Access subject row lock은 subject 상태 mutation끼리는 직렬화하되 permission FK의 `KEY SHARE`와 호환되는 `FOR NO KEY UPDATE` 또는 동등한 강도를 사용한다. 요청이 current primary를 관찰한 뒤 primary 전환에 밀리면 `409 workflow.primary_changed`로 재시도를 요구하고 old Workflow만 변경한 성공을 반환하지 않는다. Organization member 제거는 subject lock 이후 그 사용자의 Workflow permission scope 집합을 안정될 때까지 재조회·잠근 뒤 bulk revoke하여, primary 전환 중 새 target Workflow에 승계된 grant도 남기지 않는다. 요청 시작부터 이미 non-primary였던 Workflow의 독립 권한 관리는 유지한다.
- Team knowledge permission model은 KB permission 관리 API/UI의 일부다. Collection permission과 KB content permission은 서로 다른 권한 surface이며, KB permission 부여가 Collection route/manage 권한을 자동 부여하지 않는다.
- 기본 organization foundation은 `Default` team 하나를 만든다. data model의 Admin/Builder/Operator/Viewer/Auditor team template preset 자동 생성은 현재 구현 범위가 아니다.
- 멤버 초대 API는 email invitation이 아니라 가입된 user UUID 기반 초대다. email 검색/초대 UX는 현재 구현 범위가 아니다.
- Sidebar 알림 overlay MVP는 이미 `/dashboard`에 진입해 Sidebar를 볼 수 있는 사용자를 대상으로 한다. active organization 없이 invited membership만 가진 신규 user flow는 현재 구현 범위가 아니다.
- Admin console은 organization 관리의 주 UI다. Settings page가 access-management surface를 노출하는 경우에도 workflow/KB/LLM permission semantics는 Admin console과 동일해야 하며, KB direct grant/revoke를 다른 의미로 재정의하지 않는다.
- 클라이언트의 manager-only 메뉴 숨김은 UX 차단이다. 최종 보안 판단은 Gateway endpoint와 shared permission helper가 수행한다.
- Audit metadata에는 actor snapshot, request metadata, permission cleanup count, resource id가 포함될 수 있다. secret value, raw credential, token 원문은 기록하지 않는다.

## Open Questions

- 별도 organization 생성/비활성화 API를 제공할지, 또는 auth 기반 기본 organization 생성만 유지할지 결정해야 한다.
- email 기반 멤버 초대와 user directory 검색을 organization 범위에 포함할지 결정해야 한다.
- data model에 있는 Team Template preset(Admin/Builder/Operator/Viewer/Auditor)을 실제 자동 생성할지 결정해야 한다.
- Knowledge permission grant/revoke와 audit permission grant/revoke를 organization 관리 API/UI 범위에 포함할지 결정해야 한다.
- Settings page의 숨겨진 access-management 코드를 제거할지, admin console과 통합할지 결정해야 한다.
