# Organization API Spec

Status: Draft
Verified Against: feature/mba-136 @ 9819d16a

검증 값은 actor access profile/team/resource/action과 MBA-188이 보강한 member/team/user-direct/App 권한 mutation 계약에 적용한다. 다른 organization endpoint는 각 구현 이력의 기준을 따른다.

기본 경로: `/api/v1`

## Common Contracts

대부분의 organization scope API는 `auth_token` HTTP-only cookie 인증을 사용하며, organization scope가 필요한 요청은 `X-Organization-Id` header로 active organization을 명시한다.

`X-Organization-Id`가 필요한 endpoint에서 header가 없으면 `400 organization.required`, UUID 형식이 아니면 `422 validation.failed`를 반환한다.

오류 envelope:

```json
{
  "error": {
    "code": "permission.denied",
    "message": "Permission denied.",
    "request_id": "...",
    "details": {}
  }
}
```

## Endpoints

### Organization Context

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/organizations` | 현재 사용자가 active context로 사용할 수 있는 active organization 목록을 반환한다. | `auth_token` cookie |
| GET | `/organizations/memberships` | 현재 사용자의 active/invited organization membership 요약을 반환한다. | `auth_token` cookie |
| GET | `/organizations/current` | `X-Organization-Id`로 지정한 현재 organization 상세를 반환한다. | `auth_token` cookie, `X-Organization-Id` |
| GET | `/organizations/{organization_id}` | 현재 사용자가 접근 가능한 organization 상세를 반환한다. | `auth_token` cookie |
| PATCH | `/organizations/{organization_id}` | organization 이름/options를 수정한다. | `auth_token` cookie, manager, `X-Organization-Id` |

### Organization Membership

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/organizations/{organization_id}/members` | organization member 목록을 반환한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| POST | `/organizations/{organization_id}/members/invitations` | 가입된 user id를 organization member로 초대한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| POST | `/organizations/{organization_id}/members/me/accept` | 현재 로그인한 사용자가 해당 organization에서 자신에게 온 초대를 수락한다. | `auth_token` cookie |
| POST | `/organizations/{organization_id}/members/me/decline` | 현재 로그인한 사용자가 해당 organization에서 자신에게 온 초대를 거절한다. | `auth_token` cookie |
| PATCH | `/organizations/{organization_id}/members/{user_id}` | member 상태 또는 organization auth state를 변경한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| DELETE | `/organizations/{organization_id}/members/{user_id}` | member를 removed 상태로 바꾸고 team/user direct permission 및 App 생성 권한 row를 정리한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| GET | `/organizations/{organization_id}/members/{user_id}/access-profile` | member의 membership/team/App-creation/access source summary를 반환한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| GET | `/organizations/{organization_id}/members/{user_id}/team-memberships` | member의 active/inactive team membership을 paginated 반환한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| GET | `/organizations/{organization_id}/members/{user_id}/resource-access` | member의 direct/team resource access를 resource별로 반환한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |
| POST | `/organizations/{organization_id}/members/{user_id}/access-actions` | member access 항목 하나를 변경하고 canonical audit을 기록한다. | `auth_token` cookie, manager, matching `X-Organization-Id` |

### Notifications

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/notifications` | 현재 사용자의 organization 초대 알림 목록을 반환한다. | `auth_token` cookie |
| GET | `/notifications/stream` | 현재 사용자의 notification SSE stream을 연다. | `auth_token` cookie |

### Permission Requests

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| POST | `/permission-requests` | 현재 사용자가 App 생성 권한(`app.create`)을 신청한다. | `auth_token` cookie, `X-Organization-Id` |

### Teams

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/teams` | active organization의 team 목록을 반환한다. | `auth_token` cookie, manager, `X-Organization-Id` |
| POST | `/teams` | active organization에 team을 생성한다. | `auth_token` cookie, manager, `X-Organization-Id` |
| PATCH | `/teams/{team_id}` | active team을 수정한다. | `auth_token` cookie, manager, `X-Organization-Id` |
| DELETE | `/teams/{team_id}` | team을 비활성화한다. | `auth_token` cookie, manager, `X-Organization-Id` |

### Team Membership

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/teams/{team_id}/members` | team member 목록을 반환한다. | `auth_token` cookie, manager, `X-Organization-Id` |
| POST | `/teams/{team_id}/members` | active organization member를 team에 추가한다. | `auth_token` cookie, manager, `X-Organization-Id` |
| DELETE | `/teams/{team_id}/members/{user_id}` | user를 team에서 제거한다. | `auth_token` cookie, manager, `X-Organization-Id` |

### Resource Permission Read

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| GET | `/permissions/workflows/{workflow_id}` | workflow에 부여된 team/user direct permission 목록을 반환한다. | `auth_token` cookie, manager 또는 workflow manage, `X-Organization-Id` |
| GET | `/permissions/knowledge-bases/{knowledge_base_id}` | Knowledge Base에 부여된 team/user direct permission 목록을 반환한다. | `auth_token` cookie, manager 또는 KB manage, `X-Organization-Id` |
| GET | `/permissions/llm-credentials/{credential_id}` | LLM credential에 부여된 team/user direct permission 목록을 반환한다. | `auth_token` cookie, manager 또는 credential manage, `X-Organization-Id` |

### Resource Permission Grant

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| PUT | `/permissions/workflows/{workflow_id}/teams/{team_id}` | team workflow permission을 생성하거나 갱신한다. | `auth_token` cookie, manager 또는 workflow manage, `X-Organization-Id` |
| PUT | `/permissions/workflows/{workflow_id}/users/{user_id}` | user direct workflow permission을 생성하거나 갱신한다. | `auth_token` cookie, manager 또는 workflow manage, `X-Organization-Id` |
| PUT | `/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}` | team KB permission을 생성하거나 갱신한다. | `auth_token` cookie, manager 또는 KB manage, `X-Organization-Id` |
| PUT | `/permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}` | user direct KB permission을 생성하거나 갱신한다. | `auth_token` cookie, manager 또는 KB manage, `X-Organization-Id` |
| PUT | `/permissions/llm-credentials/{credential_id}/teams/{team_id}` | team LLM credential permission을 생성하거나 갱신한다. | `auth_token` cookie, manager 또는 credential manage, `X-Organization-Id` |
| PUT | `/permissions/llm-credentials/{credential_id}/users/{user_id}` | user direct LLM credential permission을 생성하거나 갱신한다. | `auth_token` cookie, manager 또는 credential manage, `X-Organization-Id` |
| POST | `/permissions/bulk-grants` | Workflow, Knowledge Base, LLM/Mail credential의 복수 resource×grantee permission을 원자적으로 생성하거나 갱신한다. | `auth_token` cookie, 모든 target의 manage 또는 organization manager, `X-Organization-Id` |

### Resource Permission Revoke

| 메서드 | 경로 | 설명 | 인증 |
| --- | --- | --- | --- |
| DELETE | `/permissions/workflows/{workflow_id}/teams/{team_id}` | team workflow permission을 삭제한다. | `auth_token` cookie, manager 또는 workflow manage, `X-Organization-Id` |
| DELETE | `/permissions/workflows/{workflow_id}/users/{user_id}` | user direct workflow permission을 삭제한다. | `auth_token` cookie, manager 또는 workflow manage, `X-Organization-Id` |
| DELETE | `/permissions/knowledge-bases/{knowledge_base_id}/teams/{team_id}` | team KB permission을 삭제한다. | `auth_token` cookie, manager 또는 KB manage, `X-Organization-Id` |
| DELETE | `/permissions/knowledge-bases/{knowledge_base_id}/users/{user_id}` | user direct KB permission을 삭제한다. | `auth_token` cookie, manager 또는 KB manage, `X-Organization-Id` |
| DELETE | `/permissions/llm-credentials/{credential_id}/teams/{team_id}` | team LLM credential permission을 삭제한다. | `auth_token` cookie, manager 또는 credential manage, `X-Organization-Id` |
| DELETE | `/permissions/llm-credentials/{credential_id}/users/{user_id}` | user direct LLM credential permission을 삭제한다. | `auth_token` cookie, manager 또는 credential manage, `X-Organization-Id` |

Workflow permission PUT/DELETE는 App lifecycle row를 먼저 잠그고 요청 도중 primary가 교체됐는지 재검증한다. 요청이 current primary를 관찰한 뒤 교체에 밀린 경우 `409 workflow.primary_changed`를 반환하며 permission row와 audit을 변경하지 않는다. 요청 시작부터 이미 non-primary였던 Workflow의 permission 관리는 기존 Workflow-scoped 계약을 유지한다.

## Request And Response Models

### `GET /organizations`

요청 본문: 없음.

성공 응답: `200 OK`, `OrganizationResponse[]`.

반환 대상은 현재 사용자가 active membership으로 접근할 수 있는 active organization만 포함한다. invited organization은 이 endpoint에 포함하지 않는다.

### `GET /organizations/memberships`

요청 본문: 없음.

성공 응답: `200 OK`, `OrganizationSummaryResponse[]`.

이 endpoint는 active와 invited membership을 함께 반환한다. removed/suspended membership은 포함하지 않는다.

### `GET /organizations/current`

요청 header:

| 이름 | 필수 | 비고 |
| --- | --- | --- |
| `X-Organization-Id` | 예 | 현재 작업 organization UUID |

성공 응답: `200 OK`, `OrganizationResponse`.

### `GET /organizations/{organization_id}`

요청 본문: 없음.

성공 응답: `200 OK`, `OrganizationResponse`.

### `POST /permission-requests`

요청 header:

| 이름 | 필수 | 비고 |
| --- | --- | --- |
| `X-Organization-Id` | 예 | 권한을 신청할 organization UUID |

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `requested_permission` | `string` | 아니오 | 생략 시 `app.create`. 다른 값은 거부한다. |
| `reason` | `string` | 예 | blank 값을 거부한다. |

성공 응답: `201 Created`, `PermissionRequestResponse`. 제출 응답의 `user`는 null이다.

거부 조건:

- 이미 App 생성 권한을 보유한 사용자(owner/manager 또는 `user_app_creation_permissions` row 보유)는 `409`, `{"detail": "App creation permission already granted"}`를 반환한다.
- 같은 organization에 pending `app.create` 신청이 있으면 `409`, `{"detail": "Pending permission request already exists"}`를 반환한다.
- blank `reason`과 `app.create`가 아닌 `requested_permission`은 `422 validation.failed`로 거부한다.
- active organization membership이 아니면 active organization context 판정에서 거부한다.

두 `409` 응답은 오류 envelope가 아니라 `{"detail": <string>}` 형태다. 클라이언트는 `detail` 문자열로 이미 권한 보유 상태와 pending 신청 중복을 구분해 안내한다.

### `PATCH /organizations/{organization_id}`

요청 header:

| 이름 | 필수 | 비고 |
| --- | --- | --- |
| `X-Organization-Id` | 예 | path `organization_id`와 같아야 한다. |

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `name` | `string \| null` | 아니오 | 제공되면 blank 값을 거부한다. 최대 255자. |
| `options` | `object \| null` | 아니오 | 제공되면 null 값을 거부한다. |

성공 응답: `200 OK`, `OrganizationResponse`.

### `GET /organizations/{organization_id}/members`

요청 header: matching `X-Organization-Id`.

Query parameter:

| 이름 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `state` | `string` | 아니오 | `active`, `invited`, `suspended`, `removed` 중 하나. 없으면 active/invited/suspended를 반환한다. |

성공 응답: `200 OK`, `OrganizationMemberListItemResponse[]`.

- 각 item의 `current_month_usage`는 이번 달 KST 반개구간 `[month_start, next_month_start)`의 사용자별 비용 묶음이다.
- 비용은 current membership state와 무관하게 item의 `user_id`에 귀속된 eligible usage를 합산한다. Legacy usage는 operation reference가 없는 기존 `user_id`, canonical provider usage는 명시적인 user형 `execution_subject`를 사용한다. Credential principal, billing principal, audit actor를 member user로 대체하지 않으며 public/system 실행을 임의 사용자에게 합성하지 않는다. 따라서 `invited`, `suspended`, `removed` member도 그 달 usage가 있으면 비용을 반환하며, usage가 없을 때만 0을 반환한다.
- eligible usage의 App primary workflow, organization/legacy NULL, Agent Builder 성공 행, 타 organization 제외 정책은 `GET /admin/usage/workflows`와 동일하다. `total_cost = workflow_execution_cost + agent_builder_cost`를 만족한다.
- 해당 user의 기간 내 `provider_started`/`outcome_unknown` operation이 있으면 확정 비용은 유지하면서 `usage_data_complete=false`와 `unresolved_provider_call_count`를 반환한다.

### `POST /organizations/{organization_id}/members/invitations`

요청 header: matching `X-Organization-Id`.

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `user_id` | `UUID` | 예 | 이미 가입된 active user id. |
| `organization_auth_state` | `member \| manager` | 아니오 | 기본값은 `member`. |

성공 응답: `200 OK`, `OrganizationMemberResponse`.

현재 API는 email invitation이 아니라 user UUID 기반 초대다.

### `POST /organizations/{organization_id}/members/me/accept`

요청 본문: 없음.

성공 응답: `200 OK`, `OrganizationMemberResponse`.

이 endpoint는 초대받은 현재 사용자 본인의 수락 경로이며 manager 권한을 요구하지 않는다.

### `POST /organizations/{organization_id}/members/me/decline`

요청 본문: 없음.

성공 응답: `200 OK`, `OrganizationMemberResponse`.

이 endpoint는 초대받은 현재 사용자 본인의 거절 경로이며 manager 권한을 요구하지 않는다. 성공 시 membership은 `removed`가 되고 `organization.member.decline` audit을 기록한다. invitation이 없으면 `404`, 현재 상태가 `invited`가 아니면 `409`를 반환한다.

### `GET /notifications`

요청 본문: 없음.

성공 응답: `200 OK`.

```json
{
  "items": [
    {
      "id": "organization_invitation:<membership_id>",
      "type": "organization.invitation",
      "organization_id": "<uuid>",
      "organization_name": "Acme",
      "organization_auth_state": "member",
      "created_at": "<datetime>"
    }
  ]
}
```

이 endpoint는 별도 invitation notification table을 만들지 않고 현재 user의 `invited` organization membership만 파생한다. Active/suspended/removed membership과 Security Alert는 포함하지 않는다. Security Alert open count와 최근 item은 owner/manager가 [Security Alert API](../security-alert/api_spec.md)의 `/api/v1/admin/security-alerts/summary`로 별도 조회한다.

### `GET /notifications/stream`

SSE 응답: `text/event-stream`.

응답 header:

| Header | 값 | 비고 |
| --- | --- | --- |
| `Cache-Control` | `no-cache, no-transform` | 중간 프록시가 SSE 응답을 캐싱하거나 변형하지 않도록 한다. |
| `X-Accel-Buffering` | `no` | nginx 응답 버퍼링 비활성화 힌트. |
| `Connection` | `keep-alive` | SSE 연결 유지. |

현재 구현 event:

```text
event: notifications.changed
data: {}
```

초대 생성/수락/거절 또는 관리자가 invited membership을 제거한 transaction이 commit된 이후 대상 사용자 channel에 발행된다. Active/suspended/already removed membership 제거에는 발행하지 않는다. Notification publish 실패는 이미 commit된 membership 변경을 rollback하지 않는다. Security Alert 생성·활성 alert 갱신·lifecycle 변경도 권한 있는 현재 organization manager 대상 notification 갱신을 발행할 수 있다. 클라이언트는 event payload를 source of truth로 사용하지 않고 `GET /notifications`와, 현재 owner/manager인 경우 `/api/v1/admin/security-alerts/summary`를 source별로 재조회한다. Security Alert publish 실패와 reconnect 복구 경계는 [Security Alert API spec](../security-alert/api_spec.md)이 소유한다.

### `PATCH /organizations/{organization_id}/members/{user_id}`

요청 header: matching `X-Organization-Id`.

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `membership_state` | `active \| suspended \| null` | 아니오 | invited/removed로 직접 PATCH할 수 없다. |
| `organization_auth_state` | `member \| manager \| null` | 아니오 | organization-level 권한이다. |

성공 응답: `200 OK`, `OrganizationMemberResponse`.

### `DELETE /organizations/{organization_id}/members/{user_id}`

요청 header: matching `X-Organization-Id`.

성공 응답: `200 OK`, `OrganizationMemberRemoveResponse`.

제거 전 membership이 `invited`인 경우 commit 성공 후 대상 사용자에게 `notifications.changed`를 발행한다. 이미 commit된 제거는 notification publish 실패로 rollback하지 않는다.

```json
{
  "status": "removed",
  "removed_team_memberships": 2,
  "revoked_user_permissions": {
    "workflow": 1,
    "llm_credential": 1,
    "knowledge_base": 0,
    "audit": 0
  }
}
```

### `GET /organizations/{organization_id}/members/{user_id}/access-profile`

요청 header: matching `X-Organization-Id`.

성공 응답: `200 OK`, `MemberAccessProfileResponse`.

```json
{
  "member": {
    "membership_id": "<uuid>",
    "user_id": "<uuid>",
    "name": "<string>",
    "email": "<string>",
    "user_active": true,
    "membership_state": "active",
    "organization_auth_state": "member",
    "updated_at": "<datetime>"
  },
  "control": {
    "is_self": false,
    "is_last_active_manager": false,
    "manager_override": false,
    "actions": {
      "membership_suspend": { "allowed": true, "reason": null },
      "membership_reactivate": { "allowed": false, "reason": "member_state_not_applicable" },
      "organization_role_set": {
        "member": { "allowed": false, "reason": "member_state_not_applicable" },
        "manager": { "allowed": true, "reason": null }
      },
      "team_membership_add": { "allowed": true, "reason": null },
      "team_membership_remove": { "allowed": true, "reason": null },
      "direct_permission_grant": { "allowed": true, "reason": null },
      "direct_permission_revoke": { "allowed": true, "reason": null },
      "app_creation_grant": { "allowed": true, "reason": null },
      "app_creation_revoke": { "allowed": true, "reason": null }
    }
  },
  "effective_access_enabled": true,
  "team_membership_count": 1,
  "app_creation": {
    "effective": true,
    "effective_source": "direct",
    "direct_permission": {
      "permission_id": "<uuid>",
      "assigned_at": "<datetime>"
    }
  },
  "permission_counts": {
    "direct": 3,
    "team_inherited": 4
  }
}
```

각 leaf control의 `reason`은 `self_control_forbidden`, `last_active_manager`, `manager_override_active`, `member_state_not_manageable`, `member_state_not_applicable`, `target_user_inactive` 중 하나 또는 null이다. `organization_role_set`은 desired `member`/`manager`별 control을 따로 반환하고 나머지는 action별 control을 반환한다.

- Caller는 ADR-0009의 organization manager 판정을 통과해야 한다. Membership row가 있으면 active manager membership이 우선하며, row가 없을 때만 legacy `created_by`/`managed_by` fallback을 허용한다.
- Target profile은 current organization의 active 또는 suspended membership만 반환한다. Invited/removed/missing target은 historical actor와 동일하게 `404 resource.not_found`로 숨긴다.
- Globally deactivated target도 active/suspended membership이 있으면 cleanup profile을 반환한다. `member.user_active=false`, `effective_access_enabled=false`이며 global account reactivation은 이 API 범위가 아니다.
- `manager_override`는 target user가 globally active이고 active membership + manager role일 때만 true다. Globally deactivated 또는 suspended target의 stored manager role은 override가 아니다.
- `app_creation.effective_source`는 `manager_override`, `direct`, `none` 중 하나다. `direct_permission`은 effective source와 별개로 stored row를 나타내며 없으면 null이다.
- `permission_counts.direct`는 `none`을 제외한 direct allow row 수, `team_inherited`는 active team을 통해 연결된 distinct `(resource_type, resource_id, team_id)` allow source 수다. Suspended 상태에서도 stored source count는 유지된다.
- `team_membership_count`는 active/inactive stored team membership row의 합계다. 실제 row는 paginated team-memberships endpoint에서 조회한다.

### `GET /organizations/{organization_id}/members/{user_id}/team-memberships`

Authorization과 target eligibility는 access-profile endpoint와 같다. Active/suspended membership의 globally deactivated target도 cleanup 목록을 반환한다.

Query parameter:

| 이름 | 타입 | 필수 | 기본값 | 비고 |
| --- | --- | --- | --- | --- |
| `teamId` | UUID | 아니오 | - | 특정 catalog team의 stored membership 존재 여부를 exact 조회한다. |
| `page` | integer | 아니오 | 1 | 1 이상. |
| `limit` | integer | 아니오 | 20 | 1 이상 100 이하. |

성공 응답: `200 OK`, `MemberTeamMembershipListResponse`.

```json
{
  "total": 1,
  "items": [
    {
      "team_membership_id": "<uuid>",
      "team_id": "<uuid>",
      "name": "Default",
      "is_active": true,
      "assigned_at": "<datetime>",
      "inherited_resource_counts": {
        "workflow": 2,
        "knowledge_base": 1,
        "llm_credential": 0,
        "total": 3
      }
    }
  ]
}
```

Current organization의 active/inactive team membership row를 team name과 id로 안정 정렬한다. `inherited_resource_counts`는 조회 snapshot에서 active team의 operational allow permission에 연결되는 distinct resource source 수를 type별/전체로 집계하며 inactive team은 모두 0이다. 이는 team remove로 사라지는 source impact이지, direct/다른 team source까지 반영한 effective access loss 수나 mutation precondition이 아니다. Page에 선택된 team id 집합을 grouped batch로 집계하고 item별 count query를 실행하지 않는다. Inactive team은 stored membership 정리 대상으로만 표시하고 effective permission source나 add 대상에는 포함하지 않는다. `total`은 pagination 전 membership row 수다.

`teamId`가 있으면 나머지 scope/lifecycle 규칙은 유지한 채 해당 team membership만 필터링하며 `total`은 0 또는 1이다. Paginated UI는 catalog team 추가 confirm을 만들기 전에 이 exact 조회로 다른 page에 이미 존재하는 membership을 확인한다. 이 값은 authorization source가 아니며 mutation endpoint가 row 존재와 precondition을 다시 검증한다.

### `GET /organizations/{organization_id}/members/{user_id}/resource-access`

Authorization과 target eligibility는 access-profile endpoint와 같다. Globally deactivated 또는 suspended target은 stored source를 조회할 수 있지만 effective state는 `none`이다.

Query parameter:

| 이름 | 타입 | 필수 | 기본값 | 비고 |
| --- | --- | --- | --- | --- |
| `resourceType` | `workflow \| knowledge_base \| llm_credential` | 예 | - | 중앙 resource permission registry에 등록된 operational resource만 허용한다. |
| `resourceId` | UUID | 아니오 | - | 선택한 catalog resource의 current source projection을 exact 조회한다. |
| `source` | `all \| direct \| team` | 아니오 | `all` | direct와 team-inherited source filter. |
| `page` | integer | 아니오 | 1 | 1 이상. |
| `limit` | integer | 아니오 | 20 | 1 이상 100 이하. |

성공 응답: `200 OK`, `MemberResourceAccessListResponse`.

```json
{
  "total": 1,
  "items": [
    {
      "resource_type": "knowledge_base",
      "resource_id": "<uuid>",
      "resource_name": "Support KB",
      "effective_auth_state": "builder",
      "direct_permission": {
        "permission_id": "<uuid>",
        "auth_state": "viewer",
        "assigned_at": "<datetime>"
      },
      "team_sources": [
        {
          "team_membership_id": "<uuid>",
          "team_id": "<uuid>",
          "team_name": "Builders",
          "auth_state": "builder"
        }
      ]
    }
  ]
}
```

Resource는 이름과 id로 안정 정렬하고 id를 tie-break로 사용한다. Archived/deleted KB와 lifecycle상 숨겨야 하는 resource는 각 feature의 resource hiding 정책에 따라 제외하거나 404로 처리한다.

`total`은 `resourceType`, `source`, lifecycle/scope filter를 적용한 distinct resource projection 수이며 현재 page item 수가 아니다.

이 endpoint는 current direct 또는 active team source가 하나 이상 존재하는 resource projection만 반환한다. `source` filter는 row 포함 조건이며, 포함된 row에는 effective state 설명을 위해 direct와 team source 전체를 반환한다. Direct re-grant의 resource picker는 이 응답을 전체 catalog로 사용하지 않고 기존 organization-scoped workflow/KB/LLM credential 목록을 사용한다.

Pagination은 filtered distinct resource id/name 집합에 먼저 적용하고, 선택된 page id의 direct/team source를 bounded batch로 조회한다. Source join row에 직접 offset/limit을 적용해 duplicate resource가 page를 왜곡하거나 item별 source query로 N+1을 만들면 안 된다.

`resourceId`가 있으면 lifecycle/scope/source filter를 모두 적용한 뒤 해당 resource projection만 반환하며 `total`은 0 또는 1이다. Catalog picker는 grant/update confirm 전에 `source=all`, `resourceId`, `page=1`, `limit=1`로 exact current direct row id/auth state를 확인한다. 다른 page의 existing row를 `expected_absent` create로 오판해서는 안 되며, mutation endpoint가 exact 조회 이후의 race를 optimistic precondition으로 다시 차단한다.

`effective_auth_state`는 target global user/membership gate와 active manager override까지 적용한 최종 값이다. Globally deactivated 또는 suspended target은 stored `direct_permission`/`team_sources`를 반환하더라도 `effective_auth_state="none"`이고, globally active + active manager target은 listed source보다 강한 `manager`다. Legacy workflow/LLM user-direct `none` row는 actor가 직접 revoke할 수 있으므로 cleanup용 `source=direct` projection과 `total`에 포함하지만 allow count나 effective allow source로 합산하지 않는다. Team permission의 `none` 또는 operational allow가 아닌 state는 actor `team_sources`, projection, count에서 제외하고 resource-centric permission UI에서 관리한다.

### `POST /organizations/{organization_id}/members/{user_id}/access-actions`

한 요청은 정확히 하나의 action만 수행한다. 모든 variant는 다음 common field를 가진다.

| 필드 | 타입 | 필수 | 의미 |
| --- | --- | --- | --- |
| `action` | string discriminator | 예 | 아래 지원 action 중 하나. |
| `expected_membership_id` | UUID | 예 | Profile에서 받은 target membership row id. |
| `expected_user_active` | boolean | 예 | Confirm 시점 target global user active state. |
| `expected_membership_state` | `active \| suspended` | 예 | Confirm 시점 target state. |
| `expected_organization_auth_state` | `member \| manager` | 예 | Confirm 시점 target role. |
| `reason` | string 또는 null | 아니오 | CRLF/CR을 LF로 정규화하고 trim한 뒤 blank는 null. 정규화 후 최대 500 Unicode code point. |

`reason`은 tab/LF 외 C0/C1 control과 bidi override/isolate control을 거부한다. Durable audit 저장 전 organization 설정으로 약화할 수 없는 shared fail-closed redaction baseline으로 secret/PII pattern을 치환하며 sanitization 실패 시 raw reason과 mutation을 저장하지 않는다.

Action variant는 `extra="forbid"`로 정의해 다른 variant 전용 field를 거부한다. `expected_absent`는 literal `true`만 허용한다.

지원 action:

| `action` | 추가 target field | 추가 precondition | 의미 |
| --- | --- | --- | --- |
| `membership.suspend` | 없음 | `expected_membership_state="active"` | active membership을 suspended로 변경한다. |
| `membership.reactivate` | 없음 | `expected_membership_state="suspended"` | suspended membership을 active로 변경한다. |
| `organization_role.set` | `role: member \| manager` | common membership snapshot | organization auth state를 변경한다. Suspended target은 manager-to-member cleanup만 허용하고 promotion은 409다. |
| `team_membership.add` | `team_id` | `expected_absent=true` | Active target을 active team에 추가한다. |
| `team_membership.remove` | `team_id` | `expected_team_membership_id` | Team membership row를 제거한다. Suspended/globally inactive target과 inactive team membership cleanup도 허용한다. |
| `direct_permission.grant` | `resource_type`, `resource_id`, `auth_state: viewer \| operator \| builder \| manager` | Create는 `expected_absent=true`, update는 `expected_permission_id` + `expected_auth_state` | Canonical direct permission을 생성/갱신한다. `none`은 거부하고 revoke를 사용한다. |
| `direct_permission.revoke` | `resource_type`, `resource_id` | `expected_permission_id` + `expected_auth_state` | Direct permission row를 삭제한다. Suspended target cleanup도 허용한다. |
| `app_creation.grant` | 없음 | `expected_absent=true` | Active target의 App creation permission row를 생성한다. |
| `app_creation.revoke` | 없음 | `expected_permission_id` | App creation permission row를 삭제한다. Suspended target cleanup도 허용한다. |

`direct_permission.grant`의 create/update precondition 조합은 상호 배타적이다. 모든 row id와 expected state는 authorization source가 아니라 lost-update 방지값이며, server는 path/header organization과 target/resource/team scope를 별도로 다시 검증한다.

`expected_auth_state`는 existing legacy row 검증을 위해 `none`도 받을 수 있다. Desired `auth_state`는 `none`을 받을 수 없다.

Suspended target은 `membership.reactivate`, manager-to-member `organization_role.set`, `team_membership.remove`, `direct_permission.revoke`, `app_creation.revoke`만 state-changing action으로 허용한다. Promotion과 add/grant는 먼저 membership을 재활성화해야 하며 그렇지 않으면 `409 member_state_not_manageable`이다.

Globally deactivated target은 cleanup-only다. `membership.suspend`, manager-to-member `organization_role.set`, `team_membership.remove`, `direct_permission.revoke`, `app_creation.revoke`만 state-changing action으로 허용한다. Reactivate, promotion, add/grant는 `409 target_user_inactive`다. 이미 desired state인 요청은 아래 no-op 우선순위에 따라 unchanged일 수 있다.

요청 예시:

```json
{
  "action": "membership.suspend",
  "expected_membership_id": "<uuid>",
  "expected_user_active": true,
  "expected_membership_state": "active",
  "expected_organization_auth_state": "member",
  "reason": "운영 검토"
}
```

```json
{
  "action": "direct_permission.grant",
  "expected_membership_id": "<uuid>",
  "expected_user_active": true,
  "expected_membership_state": "active",
  "expected_organization_auth_state": "member",
  "resource_type": "knowledge_base",
  "resource_id": "<uuid>",
  "auth_state": "viewer",
  "expected_permission_id": "<uuid>",
  "expected_auth_state": "operator",
  "reason": null
}
```

성공 응답: `200 OK`, `MemberAccessActionResponse`.

```json
{
  "status": "applied",
  "action": "membership.suspend",
  "target_type": "organization_membership",
  "target_id": "<uuid>",
  "effective_access_changed": true,
  "affected_resource_source_count": null
}
```

| 필드 | 타입 | 의미 |
| --- | --- | --- |
| `status` | `applied \| unchanged` | 실제 row 변경 여부. |
| `action` | string | 요청 discriminator. |
| `target_type` | `organization_membership \| team_membership \| user_workflow_permission \| user_knowledge_permission \| user_llm_permission \| user_app_creation_permission` | Canonical mutation target type. |
| `target_id` | UUID 또는 null | 적용/기존 row id. Missing team-remove unchanged처럼 row가 없으면 null. |
| `effective_access_changed` | boolean 또는 null | 단일 effective target의 before/after가 달라졌는지. Direct row가 applied여도 stronger team source가 남으면 false일 수 있고 unchanged single-target action은 false다. Team membership add/remove는 여러 resource를 건드리므로 null이다. |
| `affected_resource_source_count` | integer 또는 null | Applied team membership add/remove transaction에서 관찰한 operational resource source 수. Team permission row 자체를 precondition/lock하지 않으므로 advisory 값이며 effective access loss/gain 수가 아니다. Unchanged team action은 0, 다른 action은 null. |

Server는 caller/organization/target/resource scope를 먼저 검증하고 필요한 row 또는 uniqueness key를 lock한 뒤 다음 순서로 판정한다.

1. `expected_membership_id`와 `expected_user_active` mismatch는 no-op보다 먼저 `409 stale_state`다.
2. Existing row update/revoke에서 `expected_*_id`와 현재 row id가 다르면 desired value가 같아도 ABA로 보고 `409 stale_state`다.
3. 같은 membership row의 membership/role desired state, existing team add, missing team remove, same-row direct update의 desired auth state, same-value direct create retry, existing App grant는 `status="unchanged"`이고 audit을 만들지 않는다.
4. 그 밖의 expected membership state/role, source auth state, absence mismatch는 `409 stale_state`이며 mutation/row-level audit 없이 아래 정책의 `policy.block`만 기록한다.

Missing direct permission/App-creation row revoke는 기존 revoke 정책과 같이 `404 resource.not_found`로 숨긴다. Cross-organization 또는 hidden row와 stale row를 구분할 때 존재 노출이 생기면 404 hiding을 우선한다.

Version column은 추가하지 않는다. 같은 row의 값이 바뀌었다가 현재 expected/desired value로 돌아온 value-only ABA는 current-state semantics상 unchanged일 수 있고, row id가 바뀐 ABA만 stale로 강제한다. 중간 변경 이력은 canonical audit에서 확인한다.

Mutation과 canonical audit row는 같은 DB transaction에서 성공해야 한다. Generic audit producer에는 `audit_event_outbox`가 구현되어 있지만 이 transaction-bound canonical audit 경로를 대체하지 않는다. Actor-management 진입점을 이유로 기존 row-level canonical action과 별도 aggregate action을 중복 기록하지 않는다.

Lock 순서는 ADR-0023을 따른다. Last-manager 후보 suspend/demote는 active manager membership을 membership id 순으로 먼저 잠근 뒤 대응 User row를 같은 membership 순서로 잠그고 globally active manager 수를 다시 계산한다. 다른 action은 target membership, target User, resource/team, child row 순서로 잠근다. Overlapping legacy mutation route도 같은 coordinator/protocol을 사용하되 기존 authorization, response/status, latent row/team affiliation 관리 정책을 보존한다. Actor-only manager-override block은 legacy route 계약을 변경하지 않는다. 이 lock은 actor action 시점의 global state를 안정화하지만 이후 별도 global account deactivation을 금지하지 않는다.

Applied mutation은 response 전에 row-level canonical audit과 함께 commit한다. Membership/role과 App creation은 `action`, team membership과 user direct permission은 `data_change` category를 사용한다. Scope 안 policy block(`self_control_forbidden`, `last_active_manager`, `manager_override_active`, `member_state_not_manageable`, `target_user_inactive`, `stale_state`)은 `policy.block` + `action` category + failure status로 commit한 뒤 error response로 mapping한다. Policy block target은 scoped organization membership이며 optional resource/team id는 scope 확인 후 metadata에만 넣는다. Audit commit 실패는 500이고 mutation은 없다. Scope 안 caller 권한 부족은 `permission.denied`를 사용하며 target 검증 전에는 target-aware metadata를 남기지 않는다. 401/422/hidden 404/unchanged에는 actor access audit을 추가하지 않는다.

Block audit의 `policy_reason`은 위 error code에 `access_management.` prefix를 붙인 allowlist 값만 사용한다. `target_user_inactive`도 동일 allowlist에 포함한다. `requested_action`은 request discriminator 그대로이며 raw body, exception text, expected/current snapshot 전체는 저장하지 않는다.

### `GET /teams`

요청 header: `X-Organization-Id`.

Query parameter:

| 이름 | 타입 | 필수 | 기본값 | 비고 |
| --- | --- | --- | --- | --- |
| `limit` | `integer string` | 아니오 | `10` | 1 이상 100 이하. |

성공 응답: `200 OK`, `TeamResponse[]`. active/inactive team을 함께 반환한다.

### `POST /teams`

요청 header: `X-Organization-Id`.

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `name` | `string` | 예 | 1자 이상, 255자 이하. organization 안에서 unique. |
| `description` | `string \| null` | 아니오 | team 설명. |
| `is_auto_add` | `boolean` | 아니오 | 기본값 false. |

성공 응답: `201 Created`, `TeamResponse`.

### `PATCH /teams/{team_id}`

요청 header: `X-Organization-Id`.

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `name` | `string \| null` | 아니오 | 제공되면 blank 값을 거부한다. |
| `description` | `string \| null` | 아니오 | 명시적 null 허용. |
| `managed_by` | `UUID \| null` | 아니오 | 같은 organization scope 안의 active user만 허용한다. |
| `is_auto_add` | `boolean \| null` | 아니오 | 신규 멤버 자동 추가 flag. |

성공 응답: `200 OK`, `TeamResponse`.

### `GET /teams/{team_id}/members`

요청 header: `X-Organization-Id`.

성공 응답: `200 OK`, `TeamMemberResponse[]`.

### `POST /teams/{team_id}/members`

요청 header: `X-Organization-Id`.

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `user_id` | `UUID` | 예 | active organization membership을 가진 active user. |

성공 응답: `200 OK`.

```json
{
  "id": "00000000-0000-0000-0000-000000000000",
  "status": "added"
}
```

### `DELETE /teams/{team_id}/members/{user_id}`

요청 header: `X-Organization-Id`.

성공 응답: `200 OK`.

```json
{
  "status": "removed"
}
```

### `DELETE /teams/{team_id}`

요청 header: `X-Organization-Id`.

성공 응답: `200 OK`.

```json
{
  "status": "deactivated"
}
```

### Permission List Endpoints

`GET /permissions/workflows/{workflow_id}`, `GET /permissions/knowledge-bases/{knowledge_base_id}`, `GET /permissions/llm-credentials/{credential_id}`는 같은 응답 구조를 사용한다.

성공 응답: `200 OK`, `ResourcePermissionListResponse`.

`resource_type` 허용 값은 `workflow`, `knowledge_base`, `llm_credential`이다.

```json
{
  "resource_type": "workflow",
  "resource_id": "00000000-0000-0000-0000-000000000000",
  "organization_id": "00000000-0000-0000-0000-000000000000",
  "team_permissions": [
    {
      "id": "00000000-0000-0000-0000-000000000000",
      "grantee_type": "team",
      "grantee_id": "00000000-0000-0000-0000-000000000000",
      "grantee_name": "Builders",
      "auth_state": "builder",
      "assigned_at": "2026-07-04T00:00:00Z"
    }
  ],
  "user_permissions": []
}
```

### Permission Grant Endpoints

PUT permission endpoints는 같은 request body를 사용한다.

요청 header: `X-Organization-Id`.

요청 본문:

| 필드 | 타입 | 필수 | 비고 |
| --- | --- | --- | --- |
| `auth_state` | `none \| viewer \| operator \| builder \| manager` | 예 | workflow/LLM credential operational matrix 값. Knowledge direct user grant는 `none` 대신 DELETE revoke를 사용한다. |

Knowledge user direct grant request는 `KnowledgeDirectPermissionGrantRequest`로 다루며 `auth_state`에서 `none`을 제외한다. Team KB grant도 신규 grant request에서는 `viewer`, `operator`, `builder`, `manager`만 사용하고, 회수는 DELETE endpoint를 사용한다.

성공 응답:

- team workflow: `200 OK`, `TeamWorkflowPermissionResponse`
- user workflow: `200 OK`, `UserWorkflowPermissionResponse`
- team KB: `200 OK`, `TeamKnowledgePermissionResponse`
- user KB: `200 OK`, `UserKnowledgePermissionResponse`
- team LLM credential: `200 OK`, `TeamLLMPermissionResponse`
- user LLM credential: `200 OK`, `UserLLMPermissionResponse`

### `POST /permissions/bulk-grants`

다중 선택 권한 부여는 하나의 resource type과 grantee type 안에서 unique ID 목록을 받는다. `resource_ids × grantee_ids`는 최대 50건이며 `none`은 grant가 아닌 DELETE revoke로 표현한다. Workflow, Knowledge Base, LLM credential, Mail credential을 지원한다.

```json
{
  "resource_type": "workflow",
  "resource_ids": ["00000000-0000-0000-0000-000000000001"],
  "grantee_type": "team",
  "grantee_ids": ["00000000-0000-0000-0000-000000000002"],
  "auth_state": "builder"
}
```

서버는 ID를 정렬한 lock 순서로 모든 target의 organization scope, manage authority, active grantee, resource lifecycle와 resource-specific self-escalation 규칙을 재검증한다. 모든 pair의 permission row와 canonical row audit를 하나의 transaction에서 commit하며, 한 pair라도 실패하면 permission/audit 전체를 rollback한다.

```json
{
  "resource_type": "workflow",
  "grantee_type": "team",
  "resource_count": 2,
  "grantee_count": 3,
  "grant_count": 6
}
```

### Permission Revoke Endpoints

DELETE permission endpoints는 request body를 사용하지 않는다.

성공 응답: `200 OK`.

```json
{
  "message": "Team workflow permission deleted",
  "id": "00000000-0000-0000-0000-000000000000"
}
```

삭제 대상 종류에 따라 `message`는 `Team knowledge permission deleted`, `User knowledge permission deleted`, `Team LLM credential permission deleted`, `User workflow permission deleted`, `User LLM credential permission deleted`가 될 수 있다.

### 공통 응답 모델

`OrganizationResponse`:

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | `UUID` | organization id |
| `name` | `string` | organization 이름 |
| `options` | `object` | 확장 옵션 |
| `is_active` | `boolean` | active organization 여부 |
| `is_manager` | `boolean` | 현재 사용자가 organization manager인지 여부 |
| `created_at` | `datetime` | 생성 시각 |
| `updated_at` | `datetime` | 수정 시각 |

`OrganizationSummaryResponse`:

| 필드 | 타입 |
| --- | --- |
| `id` | `UUID` |
| `name` | `string` |
| `membership_state` | `invited \| active \| suspended \| removed` |
| `organization_auth_state` | `member \| manager` |
| `is_active` | `boolean` |

`OrganizationMemberResponse`:

| 필드 | 타입 |
| --- | --- |
| `id` | `UUID` |
| `organization_id` | `UUID` |
| `user_id` | `UUID` |
| `user_email` | `string` |
| `user_name` | `string` |
| `membership_state` | `invited \| active \| suspended \| removed` |
| `organization_auth_state` | `member \| manager` |
| `invited_by` | `UUID \| null` |
| `invited_at` | `datetime \| null` |
| `accepted_at` | `datetime \| null` |
| `removed_at` | `datetime \| null` |
| `created_at` | `datetime` |
| `updated_at` | `datetime` |

`OrganizationMemberListItemResponse`는 `OrganizationMemberResponse`에 다음 필드를 추가한다.

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `current_month_usage` | `MemberCurrentMonthUsage` | 이번 달 KST 사용자별 비용 묶음 |

`MemberCurrentMonthUsage`:

| 필드 | 타입 |
| --- | --- |
| `total_cost` | `number` |
| `workflow_execution_cost` | `number` |
| `agent_builder_cost` | `number` |
| `usage_data_complete` | `boolean` |
| `unresolved_provider_call_count` | `integer` |

`PermissionRequestResponse`:

| 필드 | 타입 | 비고 |
| --- | --- | --- |
| `id` | `UUID` | 신청 id |
| `user` | `object \| null` | `{ id, name, email }`. 제출 응답에서는 null이며 admin 목록 조회에서 채워진다. |
| `requested_permission` | `string` | 현재는 `app.create`만 사용한다. |
| `reason` | `string` | 신청 사유 |
| `status` | `pending \| approved \| rejected` | 신청 상태 |
| `created_at` | `datetime` | 신청 시각 |
| `decided_by` | `UUID \| null` | 처리한 manager |
| `decided_at` | `datetime \| null` | 처리 시각 |

`TeamResponse`:

| 필드 | 타입 |
| --- | --- |
| `id` | `UUID` |
| `organization_id` | `UUID` |
| `name` | `string` |
| `description` | `string \| null` |
| `options` | `object` |
| `flags` | `integer` |
| `created_by` | `UUID` |
| `managed_by` | `UUID \| null` |
| `is_active` | `boolean` |
| `is_auto_add` | `boolean` |
| `created_at` | `datetime` |
| `updated_at` | `datetime` |
| `deactivated_at` | `datetime \| null` |

`TeamMemberResponse`:

| 필드 | 타입 |
| --- | --- |
| `id` | `UUID` |
| `user_id` | `UUID` |
| `email` | `string` |
| `name` | `string` |
| `assigned_at` | `datetime` |

## Errors

| 상태 | 코드 / 상세 | 조건 |
| --- | --- | --- |
| 400 | `organization.required` | `X-Organization-Id` header가 필요한 endpoint에서 header가 없다. |
| 400 | `validation.failed` | 빈 organization/team PATCH, blank name, null options, invalid membership state, no update fields, self invite/update/remove 등 business validation 실패. |
| 400 | `self_control_forbidden` | actor access action으로 자기 자신을 정지하거나 강등하려는 요청. |
| 401 | `auth.required` | `auth_token` cookie가 없다. |
| 401 | `auth.invalid` | `auth_token` cookie가 유효하지 않거나 만료됐다. |
| 403 | `permission.denied` | organization scope 안에 있지만 manager/manage 권한이 부족하다. |
| 404 | `resource.not_found` | organization/resource가 없거나 current user scope 밖이다. |
| 409 | `resource.conflict` | duplicate team name, duplicate membership race, 마지막 manager 제거/강등, removed/invited/suspended 상태 전이 충돌. |
| 409 | `manager_override_active` | manager target의 direct/team/App-creation row만 변경해도 effective access가 낮아지지 않아 actor access action을 거부한다. |
| 409 | `last_active_manager` | 마지막 active manager의 정지 또는 강등을 거부한다. |
| 409 | `member_state_not_manageable` | target이 actor access action을 허용하지 않는 membership state로 바뀌었다. |
| 409 | `target_user_inactive` | Globally deactivated target에 reactivate/promotion/add/grant처럼 privilege를 늘리는 action을 요청했다. |
| 409 | `stale_state` | expected membership/role/row/auth-state/absence precondition이 최신 DB state와 다르다. |
| 409 | `App creation permission already granted` / `Pending permission request already exists` | App 생성 권한 신청 중복. envelope 없이 `{"detail": <string>}`로 반환한다. |
| 422 | `validation.failed` | UUID route/header/query/body 형식, action별 required/forbidden field, invalid `auth_state`, reason 길이/control 문자, 그 외 Pydantic validation 실패. |
| 500 | `audit.persistence_failed` | Applied mutation 또는 policy-block event의 canonical audit를 durable하게 기록하지 못했다. Raw DB/exception detail은 반환하지 않고 mutation은 rollback한다. |

## Permissions

### Target Internal Authorization Contract

Conversation Memory와 Workflow Runtime이 사용하는 organization authorization adapter는 HTTP member profile 전체나 permission ORM row를 반환하지 않는다. 각 resource item에 대해 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`만 반환한다. Source ACL이 있는 resource는 source-owning adapter가 ACL revision을 decision revision에 반영한다.

Authenticated user의 global active state, organization lifecycle, membership state/role, relevant team membership와 direct/team permission이 바뀌면 revision이 바뀐다. Anonymous public audience는 `principal_kind=anonymous_public_audience`로 평가하고 subject ID/revision을 합성하지 않는다. Conversation Access Grant와 credential/billing principal은 membership 근거가 아니다. 이 contract는 internal application port이며 public HTTP endpoint가 아니다.

### HTTP Endpoint Permission Rules

- `/organizations`, `/organizations/memberships`, `/organizations/{organization_id}`, `/organizations/{organization_id}/members/me/accept`는 현재 사용자 인증을 요구하지만 organization manager 권한은 요구하지 않는다.
- `/organizations/current`, organization PATCH, member management, team management, permission management는 `X-Organization-Id` 기반 active organization scope를 사용한다.
- organization PATCH와 member/team 관리 API는 organization manager만 허용한다.
- Member access profile/resource source/action API는 ADR-0009의 organization manager 판정을 사용한다. Membership row가 있으면 active manager membership을 요구하고, row가 없을 때만 legacy owner/manager fallback을 허용한다. Audit `auditor`/`raw_auditor` 권한만으로는 접근할 수 없다 ([ADR-0023](../../decisions/ADR-0023-audit-actor-access-management-boundary.md)).
- workflow permission 관리 API는 organization manager 또는 대상 workflow `manage` 권한 보유자를 허용한다.
- LLM credential permission 관리 API는 organization manager 또는 대상 credential `manage` 권한 보유자를 허용한다.
- path organization과 header organization이 불일치하면 `404 resource.not_found`로 숨긴다.
- scope 밖 organization/resource/team/user는 `404 resource.not_found`로 숨긴다.
- scope 안 권한 부족은 `403 permission.denied`로 응답한다.
