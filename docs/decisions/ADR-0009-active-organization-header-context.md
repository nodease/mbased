# ADR-0009: Active Organization Header Context 승인

Status: Accepted
Date: 2026-06-29 01:45 KST
Original: ADR-202606290145-active-organization-header-context
Verified Against: feature/mba-68 @ da83ac36625a7a3b1fafe5da3ef0b91ff7d42fb4 (2026-06-30 16:53:02 KST)
Related ADRs: [ADR-0001-active-organization](ADR-0001-active-organization.md)

## 배경

기존 active organization ADR은 header, cookie/session, 단일 primary organization 중 하나를 선택해야 한다고 Proposed 상태로 남겼다.

작성 당시 코드에는 organization, team, permission API에서 `X-Organization-Id` header를 읽고, 해당 organization이 현재 user의 active team membership scope 안에 있는지 검증하는 흐름이 구현되어 있었다. MBA-67 이후 scope 판정은 active `organization_memberships` row를 기준으로 전환됐고, membership row 자체가 없는 legacy owner/manager만 호환 fallback을 받는다. `GET /api/v1/organizations/current`도 서버 session에 active organization을 저장하지 않고 매 요청의 header 값을 검증한다.

## 결정

MVP 1 기준 active organization context는 request header 방식으로 승인한다.

1. API 요청은 `X-Organization-Id` header로 active organization을 전달한다.
2. 서버는 active organization을 session/cookie에 저장하지 않는다.
3. `GET /api/v1/organizations/current`는 header 값을 검증해 현재 요청의 active organization을 반환한다.
4. `GET /api/v1/organizations`는 기존 frontend 호환을 위해 active organization context 후보만 반환한다. `invited` membership은 포함하지 않는다.
5. `GET /api/v1/organizations/memberships`는 사용자의 organization membership 목록을 반환한다. 초대 수락 UX를 위해 `invited` membership을 포함할 수 있지만, `X-Organization-Id` active organization context로 인정되는 것은 `active` membership뿐이다.
6. `PATCH /api/v1/organizations/{organization_id}`는 header organization과 path organization이 일치해야 하며, organization owner/manager만 수정할 수 있다.
7. header가 없는 legacy/과도기 경로에서는 첫 active organization membership 기반 primary organization fallback을 제한적으로 사용할 수 있다.

## 구현 기준

- Organization API는 `X-Organization-Id`를 파싱하고 active organization membership scope를 검증한다. Invited/suspended/removed membership row는 fail-closed 처리한다.
- Team 관리 API와 permission grant/revoke API는 `X-Organization-Id`를 organization scope로 사용한다.
- Permission helper는 resource의 `organization_id`와 요청 organization이 다르면 fail-closed 처리한다.
- 신규 가입 또는 legacy resource 보정처럼 organization context가 아직 없는 흐름에서는 default organization bootstrap 또는 primary organization fallback을 사용할 수 있다.

## 기존 ADR과의 관계

이 ADR은 기존 Proposed ADR을 직접 수정하지 않는다. 다만 이 ADR의 승인 범위에서는 [ADR-0001-active-organization](ADR-0001-active-organization.md)의 미확정 문구보다 이 ADR이 우선한다.

## 영향

- 인증 API: [api/auth.md](../../docs_old/api/auth.md)
- Organization/RBAC API: [api/organization-rbac.md](../../docs_old/api/organization-rbac.md)
- 인증/RBAC 아키텍처: [architecture/auth-rbac.md](../../docs_old/architecture/auth-rbac.md)
- 권한 정책: [data-model/rbac-permission-policy.md](../../docs_old/data-model/rbac-permission-policy.md)
- MVP 1 구현 계획: [implementation-plan/mvp-1-development-issue-plan.md](../../docs_old/implementation-plan/mvp-1-development-issue-plan.md)

## 후속 검토

- 모든 resource CRUD API가 header organization을 일관되게 받는지 별도 회귀 테스트로 확인한다.
- 다중 organization switcher UX는 header context를 선택/전달하는 UI 문제로 분리한다.
- header 방식이 외부 API/SDK에 노출될 때 문서와 client helper를 함께 정리한다.
