# ADR-0003: User Direct Permission 도입

Status: Proposed
Date: 2026-06-27 15:59 KST
Original: ADR-202606271559-user-direct-permission
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 배경

현재 repository RBAC 문서는 organization membership과 resource 접근을 team 중심으로 모델링한다. MVP 목표 데이터 모델과 RBAC 정책 문서는 예외 권한을 위해 resource별 user direct permission table을 제안한다.

User direct permission을 도입하면 개별 사용자 예외 허용은 쉬워지지만 schema와 권한 판정 로직이 확장된다.

## 선택지

| 선택지 | 설명 | 장단점 |
| --- | --- | --- |
| team-only permission | `team_*_permissions`만 사용한다. | 단순하고 현재 문서와 가깝지만 예외 처리가 어렵다. |
| additive user direct permission | resource별 `user_*_permissions`를 추가한다. | 예외 허용을 지원하지만 schema가 늘어난다. |
| polymorphic resource permission | 하나의 generic permission table을 만든다. | 유연하지만 현재 물리 데이터 모델 보존 원칙과 충돌한다. |

## 결정

아직 확정하지 않는다. 현재 계획은 resource별 additive allow table을 선호하며, explicit deny는 도입하지 않는다.

## 영향

- 물리 데이터 모델: [data-model/physical-data-model.md](../../docs_old/data-model/physical-data-model.md)
- 권한 정책: [data-model/rbac-permission-policy.md](../../docs_old/data-model/rbac-permission-policy.md)
- 아키텍처: [architecture/auth-rbac.md](../../docs_old/architecture/auth-rbac.md)
- API 계약: [api/organization-rbac.md](../../docs_old/api/organization-rbac.md)
- 구현 계획: [implementation-plan/mvp-1-development-issue-plan.md](../../docs_old/implementation-plan/mvp-1-development-issue-plan.md)

## 후속 검토

- migration 전 architecture change로 승인한다.
- user direct permission이 team permission을 낮추거나 deny하지 못하게 보장한다.
- grant, revoke, deny event를 `audit_logs`에 기록한다.
