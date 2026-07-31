# ADR-202606291451: Team Router RBAC Service Boundary

Status: Accepted
Authority: Decision
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Created At: 2026-06-29 14:51 KST
Related ADRs: [ADR-202606290145-active-organization-header-context](ADR-202606290145-active-organization-header-context.md), [ADR-202606291315-resource-access-403-404-policy](ADR-202606291315-resource-access-403-404-policy.md)

## 배경

최신 dev 기준 `/api/v1/teams`의 등록 router는 `team.py`로 단일화되어 있다. 그러나 `team.py`가 organization owner/manager 판정, active team membership 판정, 403/404 분기, team 목록과 team member 목록 DB 조회를 직접 처리해 RBAC business rule이 controller에 남아 있었다.

MVP 1 문서는 team 생성, member 추가/제거, team 비활성화를 요구하지만 등록 router에는 비활성화 endpoint가 없었다. `TeamService.update_team()`은 `managed_by`가 active user인지까지만 확인해 같은 organization scope 밖 user도 team manager로 표시될 수 있었다.

## 선택지

1. 기존 `team.py`의 helper를 유지하고 누락된 DELETE endpoint만 추가한다.
2. `team.py` 안에서 private helper를 더 보강해 controller가 권한 판정을 계속 소유한다.
3. 등록 router는 `team.py`로 유지하되, RBAC 판정과 team/team member 조회를 `TeamService`로 이관한다.

## 결정

선택지 3을 채택한다.

1. `/api/v1/teams`의 단일 등록 router는 `apps/gateway/api/v1/endpoints/team.py`로 유지한다.
2. Team 관리 권한 판정은 `TeamService.ensure_organization_manager_scope()`와 내부 helper가 소유한다.
3. Controller는 인증, header/body parsing, response envelope 변환만 담당한다.
4. Controller가 body validation 전에 organization manager scope를 확인해야 하는 endpoint는 `TeamManagerScope`를 받아 service mutation에 전달한다. Service mutation은 scope 객체를 검증해 같은 권한 query를 반복하지 않는다.
5. Team 목록 조회는 `TeamService.list_teams()`가 수행한다. Team member 목록 조회는 `TeamService.list_members()`가 수행한다. 관리 화면에서 비활성화 상태를 확인할 수 있도록 organization scope 안의 team을 상태값과 함께 반환한다.
6. `DELETE /api/v1/teams/{team_id}`를 등록 router에 추가하고, `X-Organization-Id`와 team organization이 일치할 때만 비활성화한다.
7. 이미 inactive인 team에 대한 `DELETE /api/v1/teams/{team_id}`는 같은 organization scope 안 manager 요청이면 `{"status": "deactivated"}`를 반환하고 추가 commit은 하지 않는다.
8. `managed_by`는 active user이면서 해당 organization scope 안에 있는 user만 허용한다. MBA-67 이후 scope 판정은 active organization membership 또는 membership row 자체가 없는 legacy owner/manager fallback을 따른다.

## 근거

권한 판정을 service로 모으면 endpoint마다 owner/manager와 membership scope를 다시 구현하면서 기준이 갈라지는 문제를 줄일 수 있다. 또한 등록 router가 이미 `team.py`로 단일화된 최신 dev 기준을 유지하면서 controller/service 책임 경계를 명확히 할 수 있다.

Team 비활성화는 MVP 1 요구사항이며, organization owner/manager의 team 관리 권한 범위 안에 있다. 최신 resource 접근 ADR과 MBA-67 helper 전환은 organization scope 안 여부를 organization membership helper로 정의한다. 따라서 `managed_by`를 같은 organization scope 안 user로 제한하면 외부 user가 운영 UI나 audit 문맥에서 team manager로 보이는 문제를 막을 수 있다.

## Team 비활성화 재시도 정책

권한 정책은 비활성 user/team/organization을 일반 resource 권한 평가에서 거부한다고 정의한다. 다만 team 비활성화 API는 active team을 사용하는 일반 resource 접근이 아니라 manager가 team 상태를 inactive로 만드는 관리 명령이다.

선택지는 두 가지였다.

1. 이미 inactive인 team에 대한 비활성화 요청도 404로 숨긴다.
2. 요청 user가 organization manager이고 team이 요청 organization 안에 있으면 이미 inactive인 team도 idempotent success로 처리한다.

선택지 2를 채택한다. 이유는 admin UI 또는 client 재시도에서 같은 비활성화 명령이 반복될 수 있고, 이때 이미 목표 상태에 도달한 scoped resource를 실패로 돌리는 것보다 `{"status": "deactivated"}`를 반환하는 편이 운영상 안정적이기 때문이다. 존재하지 않거나 요청 organization 밖 team은 계속 404로 숨긴다.

## 영향 파일

- `apps/gateway/api/v1/endpoints/team.py`
- `apps/gateway/services/team_service.py`
- `apps/gateway/tests/api/test_teams_api.py`
- `apps/gateway/tests/services/test_team_service_permissions.py`
- `docs/api/organization-rbac.md`
- `docs/decisions/README.md`
- `docs/decisions/ADR-202606291451-team-router-rbac-service-boundary.md`

## 후속 검토

- Team 목록에서 inactive team을 기본 포함하는 계약이 UI 요구와 다르면 query parameter로 active-only filter를 추가한다.
- 이미 inactive인 team에 대한 비활성화 요청을 404로 바꾸는 정책이 채택되면 `TeamService.deactivate_team()`의 idempotent branch와 API 테스트를 함께 수정한다.
- Team 비활성화 시 관련 team resource permission row 정리 또는 보존 정책은 별도 issue에서 검토한다.
