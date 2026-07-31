# ADR-0006: RBAC auth_state 및 User Direct Permission 승인

Status: Accepted
Date: 2026-06-29 01:16 KST
Original: ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1
Related ADRs: [ADR-0002-auth-state-standard](ADR-0002-auth-state-standard.md), [ADR-0003-user-direct-permission](ADR-0003-user-direct-permission.md)

## 배경

기존 ADR 두 건은 `auth_state` 표준화와 user direct permission 도입을 Proposed 상태로 남겼다.

- [ADR-0002-auth-state-standard](ADR-0002-auth-state-standard.md)
- [ADR-0003-user-direct-permission](ADR-0003-user-direct-permission.md)

이후 active 요구사항, 데이터 모델, RBAC 정책, API 문서, 구현 계획은 MVP 목표 상태를 이미 `viewer/operator/builder/manager` 기반 `auth_state`와 resource별 additive user direct permission 기준으로 정렬했다. 코드도 `user_workflow_permissions`, `user_knowledge_permissions`, `user_llm_permissions`, effective permission helper, legacy auth_state compatibility mapping을 포함한다.

기존 Proposed ADR 본문을 덮어쓰면 당시 미확정 상태가 사라지므로, 이 ADR을 후속 승인 기록으로 추가한다.

## 결정

MVP 1 기준으로 아래 결정을 승인한다.

1. `auth_state`는 application-level 권한 상태로 사용한다.
2. workflow와 LLM credential 권한에는 `none`, `viewer`, `operator`, `builder`, `manager`를 표준값으로 사용한다.
3. audit visibility 권한에는 `auditor`, `raw_auditor`, `manager`를 사용한다.
4. legacy 값 `read`, `write`, `execute`, `admin`은 호환 mapping으로 해석한다.
5. user direct permission은 team permission을 보완하는 additive allow로만 동작한다.
6. explicit deny는 도입하지 않는다.
7. polymorphic `resource_permissions`, `roles`, `user_roles`는 도입하지 않는다.

## 승인 범위

| 항목 | 결정 |
| --- | --- |
| MVP 1 user direct permission | `user_workflow_permissions`, `user_llm_permissions`를 사용한다. |
| MVP 2 user direct permission | `user_knowledge_permissions`를 사용한다. |
| MVP 3 user direct permission | `user_audit_permissions`를 사용한다. |
| 권한 합산 | organization owner/manager 자동 `manager`, team permission, user direct permission 중 가장 강한 허용 상태를 적용한다. |
| deny 처리 | 권한 row 없음 또는 `auth_state='none'`은 거부한다. user direct deny는 없다. |
| legacy mapping | `read -> viewer`, `execute -> operator`, `write -> builder`, `admin -> manager`로 해석한다. |

## 기존 ADR과의 관계

이 ADR은 기존 Proposed ADR을 직접 수정하지 않는다. 다만 이 ADR의 승인 범위에서는 아래 Proposed ADR의 미확정 문구보다 이 ADR이 우선한다.

- [ADR-0002-auth-state-standard](ADR-0002-auth-state-standard.md)
- [ADR-0003-user-direct-permission](ADR-0003-user-direct-permission.md)

Active 문서에서 `auth_state` 표준값과 user direct permission을 확정 범위처럼 다루는 것은 이 ADR 이후 정합한 것으로 본다.

## 영향

- 물리 데이터 모델: [data-model/physical-data-model.md](../../docs_old/data-model/physical-data-model.md)
- 권한 정책: [data-model/rbac-permission-policy.md](../../docs_old/data-model/rbac-permission-policy.md)
- 인증/RBAC 아키텍처: [architecture/auth-rbac.md](../../docs_old/architecture/auth-rbac.md)
- Organization/RBAC API: [api/organization-rbac.md](../../docs_old/api/organization-rbac.md)
- LLM credential API: [api/llm-credentials.md](../../docs_old/api/llm-credentials.md)
- MVP 1 구현 계획: [implementation-plan/mvp-1-development-issue-plan.md](../../docs_old/implementation-plan/mvp-1-development-issue-plan.md)

## 후속 검토

- active 문서의 `Related ADRs`가 필요하면 이 ADR을 함께 참조하도록 정리한다.
- `user_audit_permissions` 구현 시 MVP별 범위가 이 ADR의 승인 범위와 맞는지 다시 확인한다.
- audit action naming은 별도 결정 또는 API 문서에서 표준화한다.
