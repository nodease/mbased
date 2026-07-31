# ADR-0001: Active Organization 결정 방식

Status: Proposed
Date: 2026-06-27 15:59 KST
Original: ADR-202606271559-active-organization
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 배경

RBAC 판정은 먼저 요청의 organization context를 결정해야 한다. 작성 당시 코드에는 첫 active team membership을 기준으로 organization을 찾는 primary organization helper가 있었다. 다중 organization 사용자가 생기면 생성 scope, 조회 scope, 권한 판정 기준이 모호해질 수 있다.

## 선택지

| 선택지 | 설명 | 장단점 |
| --- | --- | --- |
| 단일 primary organization | MVP 1에서는 첫 membership만 사용한다. | 구현이 빠르지만 다중 조직 UX가 제한된다. |
| 명시적 header | API 요청에서 organization id를 header로 전달한다. | API 계약이 명확하지만 client 변경이 필요하다. |
| cookie/session context | active organization을 session/cookie에 저장한다. | UX는 자연스럽지만 상태 관리가 추가된다. |

## 결정

아직 확정하지 않는다. MVP 1 구현 전 active organization 전달 방식을 선택해야 한다.

현재 계획은 API 명세에서 organization context 전달 방식을 먼저 확정하고, Gateway permission helper와 FE 요청 scope를 그 계약에 맞추는 것이다.

## 영향

- API 계약: [api/auth.md](../../docs_old/api/auth.md), [api/organization-rbac.md](../../docs_old/api/organization-rbac.md)
- 아키텍처: [architecture/auth-rbac.md](../../docs_old/architecture/auth-rbac.md)
- 권한 정책: [data-model/rbac-permission-policy.md](../../docs_old/data-model/rbac-permission-policy.md)
- 구현 계획: [implementation-plan/mvp-1-development-issue-plan.md](../../docs_old/implementation-plan/mvp-1-development-issue-plan.md)

## 후속 검토

- API header, cookie/session, 단일 primary organization 중 하나를 승인한다.
- App, Workflow, LLM credential 생성 scope 테스트를 추가한다.
- 다중 organization switcher를 MVP 1에 포함할지 별도 결정한다.
