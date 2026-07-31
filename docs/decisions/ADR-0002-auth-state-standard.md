# ADR-0002: auth_state 표준화

Status: Proposed
Date: 2026-06-27 15:59 KST
Original: ADR-202606271559-auth-state-standard
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

## 배경

이 ADR 작성 당시 일부 권한 경로는 `read`, `write`, `execute`, `admin` 값을 사용했다. 현재 `dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1` 코드는 `none`, `viewer`, `operator`, `builder`, `manager`, `auditor`, `raw_auditor`를 application-level 표준 상태로 사용하고, legacy `read/write/execute/admin` 값은 compatibility mapping으로 해석한다.

두 체계를 동시에 방치하면 권한 판정, UI 표시, migration, 테스트 기준이 흔들릴 수 있다.

## 선택지

| 선택지 | 설명 | 장단점 |
| --- | --- | --- |
| 기존 값 유지 | `read/write/execute/admin`을 계속 사용한다. | 코드 변경은 적지만 제품 권한 vocabulary가 약하다. |
| MVP 상태로 전환 | `viewer/operator/builder/manager`와 audit 상태를 사용한다. | 제품 의미는 명확하지만 migration과 호환 처리가 필요하다. |
| 호환 mapping | 전환 기간에는 기존 값과 새 값을 모두 해석한다. | rollout은 안전하지만 코드 경로가 늘어난다. |

## 결정

이 ADR 자체는 Proposed 상태로 남긴다. 최종 승인은 [ADR-0006-accept-rbac-auth-state-and-user-direct-permission](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md)에서 이루어졌고, 현재 active 기준은 MVP 상태를 표준으로 삼으며 기존 row의 `read/write/execute/admin`은 compatibility mapping으로 해석하는 것이다.

## 영향

- 권한 정책: [data-model/rbac-permission-policy.md](../../docs_old/data-model/rbac-permission-policy.md)
- 아키텍처: [architecture/auth-rbac.md](../../docs_old/architecture/auth-rbac.md)
- API 계약: [api/organization-rbac.md](../../docs_old/api/organization-rbac.md)
- 구현 계획: [implementation-plan/mvp-1-development-issue-plan.md](../../docs_old/implementation-plan/mvp-1-development-issue-plan.md)

## 후속 검토

- mapping 표는 후속 Accepted ADR의 `read -> viewer`, `execute -> operator`, `write -> builder`, `admin -> manager`를 따른다.
- `apps/shared/services/tracing/rbac.py`는 shared permission helper의 effective workflow auth state와 compatibility mapping을 사용한다.
- legacy 값과 신규 값 모두에 대한 테스트를 유지한다.
