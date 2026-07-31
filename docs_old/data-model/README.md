# 데이터 모델

Status: Draft
Authority: Data Model
Source of Truth: Yes
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

데이터 모델 문서는 물리 데이터 모델, 권한 정책, migration 기준의 source of truth다.

| 문서 | 역할 |
| --- | --- |
| [physical-data-model.md](physical-data-model.md) | table, 주요 column, FK/참조관계, schema extension 기준 |
| [rbac-permission-policy.md](rbac-permission-policy.md) | auth_state, permission vocabulary, 권한 판정 정책과 matrix |
| [diagrams/data-model-overview.md](diagrams/data-model-overview.md) | 물리 데이터 모델 참조관계 Mermaid 시각화 |
| [diagrams/rbac-relationships.md](diagrams/rbac-relationships.md) | RBAC 권한 table 참조관계 Mermaid 시각화 |

## 권위

- DB table 기준은 [physical-data-model.md](physical-data-model.md)가 우선한다.
- RBAC 해석 기준은 [rbac-permission-policy.md](rbac-permission-policy.md)가 우선한다.
- `diagrams/` 문서는 시각화 보조 자료이며 source of truth가 아니다.
- 삭제된 폐기 ERD 초안은 구현 기준이 아니다.
- API 계약은 [api/](../api/README.md)를 따른다.
- `auth_state` 전환과 user direct permission은 [ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission](../decisions/ADR-202606290116-accept-rbac-auth-state-and-user-direct-permission.md)에 따라 MVP 1 기준 승인된 구현 기준이다.
