# 인증 및 RBAC 아키텍처

Status: Draft
Authority: Architecture
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Related ADRs: [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md), [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-202606291451-team-router-rbac-service-boundary](../decisions/ADR-202606291451-team-router-rbac-service-boundary.md)
Background ADRs: [ADR-202606271559-active-organization](../decisions/ADR-202606271559-active-organization.md)

## 적용 경계

RBAC enforcement의 목표 경계는 Gateway endpoint와 runtime service가 함께 사용하는 재사용 가능한 service/helper logic이다. Controller가 resource permission 비즈니스 규칙을 소유하지 않는 방향으로 정렬한다.

현재 dev 구현은 이 목표 구조로 수렴 중이다. workflow/LLM credential effective permission은 `apps.shared.services.permissions`와 permission enforcement helper를 재사용한다. Team 관리 API는 등록 router를 `apps/gateway/api/v1/endpoints/team.py`로 유지하되, organization manager scope 판정과 team/team member 조회를 `TeamService`로 이관했다. user directory와 permission grant/revoke API에는 아직 endpoint/service-local DB query와 helper 조합이 남아 있다.

## Organization Context

권한 판단에는 organization context가 필요하다. MVP 1의 active organization context는 `X-Organization-Id` request header로 전달하고, 서버는 session/cookie에 active organization을 저장하지 않는다. Organization, team, permission API는 header 값이 현재 user의 organization scope 안에 있는지 검증한다. MBA-67 이후 organization scope는 `organization_memberships` row를 먼저 확인해 판정한다. Active row는 active user와 active organization 안에서만 `organization_auth_state`에 따라 member/manager가 되고, inactive organization과 invited/suspended/removed row는 fail-closed 된다. Membership row 자체가 없는 legacy `organization.created_by`/`managed_by` user만 호환 fallback으로 manager scope를 인정한다.

첫 active organization membership 기반 primary organization helper는 organization context가 없는 legacy/과도기 경로의 fallback으로만 사용한다. 승인 근거는 [ADR-202606290145-active-organization-header-context](../decisions/ADR-202606290145-active-organization-header-context.md)를 따른다.

## 권한 모델

현재 활성 데이터 모델은 organization/team permission을 기본 기준으로 하고, workflow/LLM credential의 user direct permission은 additive allow로 합산한다. `auth_state` 표준값과 user direct permission 승인 근거는 [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md)를 따른다.

물리 테이블 상세는 [data-model/physical-data-model.md](../data-model/physical-data-model.md)에 정의한다.

현재 cookie 인증은 `auth_token` HttpOnly cookie를 기준으로 하며, user session용 Bearer token dependency는 없다. Bearer secret은 public run/webhook endpoint의 app secret 인증에 사용한다.
