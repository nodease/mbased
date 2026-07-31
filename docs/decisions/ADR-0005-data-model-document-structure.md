# ADR-0005: 데이터 모델 문서 구조

Status: Superseded
Date: 2026-06-27 15:59 KST
Original: ADR-202606271559-data-model-document-structure
Verified Against: dev @ ec576b4f24155697aed8843acc6e5a3fc835f7e1

> 참고: 이 ADR이 정리한 문서 구조는 2026-07-02 문서 체계 개편으로 대체되었다. 데이터 모델 문서는 현재 [docs/data_model.md](../data_model.md) 단일 문서를 기준으로 한다. 아래 본문은 결정 당시 기록이다.

## 배경

이전 데이터 모델 source 문서명은 `final-mvp-erd-entities.md`였다. 이 이름은 MVP가 이미 단계적 산출물을 뜻한다는 점, ERD가 이미 entity relationship diagram이라는 점, 실제 문서가 다이어그램보다 물리 table, column, 참조관계, schema extension 기준을 정의한다는 점에서 혼란을 만들었다.

또한 RBAC 문서가 `rbac-permission-model.md`와 `rbac-permission-matrix.md`로 나뉘어 같은 개념의 권위가 분산되어 있었다.

## 선택지

| 선택지 | 설명 | 장단점 |
| --- | --- | --- |
| 기존 이름 유지 | 기존 파일명을 그대로 둔다. | 변경은 적지만 용어 혼란과 RBAC 중복 권위가 남는다. |
| 이름만 변경 | 파일명만 바꾸고 문서 분리는 유지한다. | 명명은 좋아지지만 RBAC 정책이 계속 둘로 나뉜다. |
| 이름 변경과 병합 | `physical-data-model.md`, `rbac-permission-policy.md`, `diagrams/` 구조로 정리한다. | source of truth가 명확해지지만 링크 갱신이 필요하다. |

## 결정

이름 변경과 병합 방식을 채택한다.

- `physical-data-model.md`는 물리 table, 주요 column, FK/참조관계, schema extension boundary의 source of truth다.
- `rbac-permission-policy.md`는 `auth_state`, permission vocabulary, resource matrix, 권한 판정 순서, enforcement point의 source of truth다.
- Mermaid 다이어그램은 `data-model/diagrams/` 아래에 두며 보조 시각화로만 사용한다.
- RBAC 구현 table은 전체 물리 데이터 모델에 포함한다. RBAC 정책 문서는 그 table들을 어떻게 해석할지 설명한다.

## 근거

이 구조는 물리 schema 권위와 시각화 문서를 분리하고, 두 active RBAC 문서가 구현 기준으로 경쟁하는 문제를 제거한다. 또한 `final MVP ERD entity`라는 모호한 표현을 없애면서 기존 아키텍처와 schema 결정을 보존한다.

## 영향

- [data-model/physical-data-model.md](../../docs_old/data-model/physical-data-model.md)
- [data-model/rbac-permission-policy.md](../../docs_old/data-model/rbac-permission-policy.md)
- [data-model/diagrams/data-model-overview.md](../../docs_old/data-model/diagrams/data-model-overview.md)
- [data-model/diagrams/rbac-relationships.md](../../docs_old/data-model/diagrams/rbac-relationships.md)
- [data-model/README.md](../../docs_old/data-model/README.md)
- [README.md](../../docs_old/README.md)
- [decisions/README.md](../../docs_old/decisions/README.md)

## 후속 검토

- table/column 변경은 `physical-data-model.md`에 반영한다.
- 권한 해석 변경은 `rbac-permission-policy.md`에 반영한다.
- Mermaid 다이어그램은 source 문서가 바뀐 뒤 갱신한다.
