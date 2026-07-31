# Audit Tracing API Spec

Status: Draft
Verified Against: feature/mba-188 @ 59d1cc51

검증 값은 actor access audit recorder, organization-scoped detail metadata와 change summary 계약에 적용한다. 다른 Knowledge/RAG trace target은 각 feature 구현 기준을 따른다.

## Gateway Query Surface

Audit/Tracing feature는 별도 화면용 중복 endpoint를 만들지 않는다. Organization-scoped audit 검색/상세의 HTTP 계약은 [Admin Dashboard API spec](../admin-dashboard/api_spec.md)이 소유한다.

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/audit-logs` | organization audit 검색/필터 | audit `auditor` 이상 |
| GET | `/api/v1/admin/audit-logs/{audit_log_id}` | sanitized audit detail와 optional change summary | audit `auditor` 이상 |

Actor access profile/team-membership/resource source/action은 [Organization API spec](../organization/api_spec.md)의 manager-only endpoint를 사용한다. Audit endpoint는 Organization/RBAC mutation을 수행하지 않는다.

Security Alert의 검색·summary·상세·safe evidence·lifecycle API는 [Security Alert API spec](../security-alert/api_spec.md)이 소유한다. `/admin/audit-logs`는 개별 audit 조회 surface이며 alert threshold, cooldown, status의 source of truth로 사용하지 않는다.

## Request And Response Models

### `AuditLogDetailResponse`

기존 audit list item field와 sanitized `audit_metadata`를 포함한다. Team membership action의 safe metadata에는 advisory `affected_resource_source_count`가 포함될 수 있다. 응답은 다음 additive field를 포함한다.

```json
{
  "change_summary": {
    "before": {
      "organization_id": "<uuid>",
      "user_id": "<uuid>",
      "membership_state": "active",
      "organization_auth_state": "member"
    },
    "after": {
      "organization_id": "<uuid>",
      "user_id": "<uuid>",
      "membership_state": "suspended",
      "organization_auth_state": "member"
    }
  }
}
```

`change_summary`는 object 또는 null이다.

| Action | Target | Allowed fields |
| --- | --- | --- |
| `organization.member.update` | `organization_membership` | `organization_id`, `user_id`, `membership_state`, `organization_auth_state` |
| `team_membership.created`, `team_membership.deleted` | `team_membership` | `grantee_organization_id`, `team_id`, `user_id` |
| `user_workflow_permission.created/updated/deleted` | `user_workflow_permission` | `grantee_organization_id`, `user_id`, `workflow_id`, `auth_state` |
| `user_knowledge_permission.created/updated/deleted` | `user_knowledge_permission` | `grantee_organization_id`, `user_id`, `knowledge_base_id`, `auth_state` |
| `user_llm_permission.created/updated/deleted` | `user_llm_permission` | `grantee_organization_id`, `user_id`, `llm_credential_id`, `auth_state` |
| `user_app_creation_permission.created`, `user_app_creation_permission.deleted` | `user_app_creation_permission` | `user_id`, `grantee_organization_id` |

Conversation Memory의 `memory.session.*`/`memory.grant.*` action은 row before/after를 노출하는 `change_summary` 대상이 아니다. Audit detail의 safe metadata allowlist는 `organization_id`, opaque `session_ref`, audience, status, safe reason, bucketed count와 request/correlation id만 허용한다. 정상 turn/summary의 `turn_ref`, `generation_ref`, usage purpose는 operational trace/metric에 두고 AuditLog detail 계약에 추가하지 않는다. Raw content, token/hash, prompt, private source reference와 provider raw error는 허용하지 않는다.

Memory lifecycle action cardinality와 actor는 다음 target contract를 따른다.

| Operation | AuditLog rows |
| --- | --- |
| Authenticated create | `memory.session.created` 1건, current user actor |
| Public create | `memory.session.created` + `memory.grant.issued` 각 1건, `actor_id=null`, `actor_type='public'` |
| Close | `memory.session.closed` 1건. Transcript-only grant 상태에서 grant rotation/issue row 없음 |
| Reset | Old `memory.session.reset` + public이면 old grant revoke + new session created + public이면 new grant issued. `closed` 중복 없음 |
| Delete request | `memory.session.delete_requested` + active public grant revoke |
| Physical erasure complete | `memory.session.purged` 1건, `actor_id=null`, `actor_type='system'` |
| `completed_with_hold` / `terminal_failure` | Purged row 없음. Operational/compliance status와 alert만 |

Retry/idempotency/reconciliation은 위 logical action을 중복 생성하지 않는다. Public request actor나 async system purge actor를 App/deployment owner, credential/billing principal이나 Access Grant reference로 대체하지 않는다. ProviderExecutionCapability와 reservation/lease raw scope는 Memory AuditLog metadata에 포함하지 않고, provider/usage operational record에도 safe opaque reference/revision과 purpose만 허용한다.

Target/action이 allowlist에 없거나 safe field가 없으면 null을 반환한다. DB `before`/`after`의 allowlist 밖 key, nested payload, secret 계열 값은 반환하지 않는다.

MBA-188 manual audit은 update에도 target별 complete safe snapshot을 저장한다. Create는 `after`, delete는 `before`, update는 `before`와 `after`의 `organization_id`/`grantee_organization_id`가 모두 request organization과 일치해야 summary를 반환한다. 필요한 snapshot의 organization provenance가 없거나 다르면 null이다. MBA-259 safe display projection은 이 raw snapshot 계약과 분리하며, current organization과 target-type allowlist를 통과한 current resource의 safe name만 별도 응답 field로 resolve한다. 삭제·미확인·미지원 resource는 opaque UUID만 유지한다.

`policy.block` failure event는 row 변경이 아니므로 `change_summary`가 null이다. Audit detail의 공통 metadata allowlist는 `organization_id`, `request_id`, sanitized `reason`, existing safe `summary`, `target_user_id`, `requested_action`, `policy_reason`, `resource_type`, `resource_id`, `team_id`, advisory `affected_resource_source_count`다. Security Alert 관리자 API의 `permission.denied`는 정확한 고정값 `required_permission=security_alert.manage`, allowlisted `requested_operation`, `denial_reason=organization_manager_required`를 추가로 허용한다. Schedule operations 전용 `operation_correlation_id`, `outcome_resolution_code`는 정확한 `schedule_dispatch.outcome_reviewed + schedule_dispatch_claim` action/target 조합에서만 추가로 허용한다. UUID field는 유효한 UUID, count는 boolean이 아닌 0 이상 integer, string field와 resource/policy reason은 정해진 scalar/enum 값일 때만 반환한다. Operation correlation은 `github-run:<decimal-id>` 또는 `k8s-job:<uuid>` 형식과 길이 제한을 통과해야 하고 outcome resolution은 `confirmed_completed`, `confirmed_failed_no_replay`, `accepted_unknown_no_replay`만 허용한다. 기존 `summary`는 secret-like key를 재귀 제거한 JSON scalar/list/object 계약을 유지하고, 그 외 허용 key의 nested object나 잘못된 타입은 생략한다. Resource/team field는 scope를 확인한 policy block 또는 applied team action에서만 기록한다. 임의 nested request body, raw URL/path/query/header, target name/email, expected/current snapshot은 포함하지 않는다.

### Safe display projection

Audit list/detail item은 기존 ID와 함께 optional `actor_display`와 `target_display`를 additive하게 반환한다.

```json
{
  "actor_id": "<uuid|null>",
  "actor_display": {
    "label": "홍길동 (hong@example.com)",
    "source": "event_snapshot"
  },
  "target_type": "workflow",
  "target_id": "<uuid|null>",
  "target_display": {
    "label": "고객문의 봇",
    "source": "current_resource"
  }
}
```

Workflow 실행과 연결된 audit item은 다음 nullable correlation을 additive하게 반환한다.

```json
{
  "workflow_run_id": "<uuid|null>",
  "workflow_node_run_id": "<uuid|null>"
}
```

- 값은 AuditLog의 typed FK projection이며 generic metadata를 그대로 공개하는 경로가 아니다.
- 기존 organization audit 권한과 scope 필터를 통과한 item에서만 반환한다.

- `source`는 `event_snapshot`, `current_resource` 중 하나다.
- User actor는 `audit_metadata.actor`의 유효한 `name`/`email` snapshot을 우선한다. Snapshot이 없으면 current organization의 member user를 batch 조회할 수 있다. System/null actor는 display object 없이 client의 고정 라벨을 사용한다.
- 1차 target allowlist는 `organization`, `user`, `team`, `workflow`, `app`, `knowledge_base`다. Workflow label은 same-organization primary App name이다.
- Target과 current-member fallback은 request organization에 속하는 row만 resolve한다. Cross-organization, hidden, deleted, malformed, unsupported reference는 display object를 생략한다.
- `resolved_references`는 detail의 allowlisted metadata/change summary에 포함된 지원 UUID를 UUID key의 display map으로 제공할 수 있다. Map에 없는 UUID는 ID-only로 렌더링하며 raw metadata를 추가 공개하지 않는다.
- List page의 current resource resolution은 target type별 batch query를 사용하며 row별 query를 허용하지 않는다. 기존 UUID filter와 ID 값은 그대로 유지한다.

`schedule_dispatch.outcome_reviewed`는 `category="action"`, `status="success"`, `actor_id=null`, `actor_type="system"`, `target_type="schedule_dispatch_claim"`, exact claim target id를 사용한다. `before`/`after`와 `change_summary`는 null이다. Claim의 durable `organization_id`가 request organization과 일치하는 audit만 list/detail에서 조회할 수 있다. Metadata의 operation correlation과 resolution은 운영 검토 완료를 설명하지만 workflow 성공/실패 재판정 또는 redrive 승인을 의미하지 않는다.

### Security Alert Evidence Projection

`GET /api/v1/admin/security-alerts/{alert_id}/audit-logs`는 alert에 실제 연결된 audit만 기존 `AuditLogSchema` 수준의 paginated safe projection으로 반환한다.

- Generic `audit_metadata`, `before`, `after`, `change_summary`를 inline 반환하지 않는다.
- Safe하지 않은 target은 `target_type`/`target_id`를 null로 내린다.
- 개별 audit detail은 기존 `/admin/audit-logs/{audit_log_id}` 권한과 allowlist를 다시 통과해야 한다.
- Security Alert endpoint는 current organization owner/manager 전용이며 audit `auditor` 권한을 재사용하지 않는다.

### `AuditRecorder` application port

HTTP model이 아니라 신규 security mutation use case가 의존하는 internal contract다.

필수 입력:

- canonical action/category
- actor id/type
- target type/id
- organization id
- optional reason
- blocked attempt의 `requested_action`, `policy_reason`
- safe before/after
- request id와 safe actor snapshot

Recorder adapter는 caller의 DB transaction에 AuditLog row를 추가하고 commit하지 않는다. MBA-188은 durable outbox를 선택지로 구현하지 않는다.

Optional reason은 Organization API spec의 normalization/validation을 통과한 뒤 organization 설정으로 약화할 수 없는 shared fail-closed redaction baseline을 적용한 값만 recorder에 전달한다. Redaction 실패 시 recorder는 raw fallback을 저장하지 않고 use case transaction을 실패시킨다.

Applied mutation은 row-level canonical action을 사용한다. Membership/role과 App creation은 `category="action"`, team membership과 user direct permission은 `category="data_change"`다. Scope 안 policy block은 `action="policy.block"`, `category="action"`, `status="failure"`, scoped organization membership target과 safe `requested_action`/`policy_reason` metadata를 사용한다. Optional resource/team id는 scope 확인 뒤에만 metadata에 포함하고 name/email/raw expected state는 저장하지 않는다. Permission 부족은 `permission.denied`이며 target scope 확인 전에는 target-aware metadata를 남기지 않는다. Block result는 audit commit 후 HTTP error로 mapping하며, hidden 404와 validation/no-op은 actor access audit을 만들지 않는다.

Actor access `policy_reason` allowlist는 `access_management.self_control_forbidden`, `access_management.last_active_manager`, `access_management.manager_override_active`, `access_management.member_state_not_manageable`, `access_management.target_user_inactive`, `access_management.stale_state`다.

## Errors

| Status | Condition |
| ---: | --- |
| 400 | audit period 또는 organization header business validation 실패 |
| 401 | 미인증 |
| 403 | organization scope 안 audit reader 권한 부족 |
| 404 | scope 밖 또는 존재하지 않는 audit log |
| 422 | UUID/query schema validation 실패 |
| 500 | Access mutation/policy block의 required audit persistence 실패. Safe `audit.persistence_failed`만 반환 |

Actor access mutation의 400/403/404/409/422는 Organization API spec을 따른다.

## Permissions

- Audit list/detail은 `auditor` 이상이다.
- Actor access profile/mutation은 ADR-0009의 organization manager 판정을 통과한 caller 전용이다.
- `raw_auditor`는 trace visibility policy의 raw access 후보일 뿐 actor mutation 권한이 아니다.
- Organization scope 밖 audit/resource는 존재를 숨긴다.
