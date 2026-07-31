# Nodease Documentation

Status: Draft
Authority: Documentation Index
Source of Truth: Yes

This directory is the active documentation root for Nodease. When active docs conflict with `docs_old/`, use this `docs/` tree as the higher-authority documentation source.

## Core Documents

| Area | Document |
| --- | --- |
| Product requirements | [PRD.md](PRD.md) |
| Architecture and service boundaries | [architecture.md](architecture.md) |
| Operational lifecycle, retention and async ownership | [operational_lifecycle.md](operational_lifecycle.md) |
| Data model and RBAC policy | [data_model.md](data_model.md) |
| Shared terminology | [glossary.md](glossary.md) |
| Design decisions | [decisions/](decisions/) |
| Feature requirements, API, components, tests | [features/](features/) |
| Protected resource feature completion and test matrix | [engineering/protected-resource-feature-completion.md](engineering/protected-resource-feature-completion.md) |
| Engineering practices and CI quality gates | [engineering/ci-quality-gates.md](engineering/ci-quality-gates.md) |
| Local demo/test DB seed guide | [demo/](demo/) |

## Authority Order

When documents conflict, use this order unless a newer accepted ADR or current code comparison explicitly overrides it:

1. Accepted ADRs in [decisions/](decisions/)
2. [PRD.md](PRD.md)
3. [architecture.md](architecture.md)
4. [data_model.md](data_model.md)
5. Feature `requirements.md`
6. Feature `api_spec.md`
7. Feature `component_spec.md`
8. Feature `test_cases.md`
9. `docs_old/` historical reference material

## Document Status

`docs/` 일반 문서의 메타 블록 `Status`는 문서 성숙도를 나타내며 아래 두 값을 사용한다. ADR은 별도 체계를 따르므로 [decisions/README.md](decisions/README.md#상태-의미)를 참조한다.

- `Draft`: 내용 정리 중이거나 검증이 끝나지 않은 상태. 해당 영역의 `docs_old/` 원본을 아직 대체하지 못한다.
- `Active`: 해당 영역의 기준 문서로 확정된 상태. Active로 승격하면 `docs_old/`의 해당 원본 문서를 삭제한다.

Active 승격 조건은 문서 종류에 따라 다르다.

- 코드 검증이 필요한 문서(architecture.md, data_model.md, feature의 `api_spec.md`/`component_spec.md`/`test_cases.md`)는 `Verified Against: <branch> @ <commit>`이 실제 코드 확인으로 채워져야 한다. `Verified Against: TBD`인 문서는 Active가 될 수 없다.
- 제품 의도를 정의하는 문서(PRD.md, glossary.md, feature의 `requirements.md`)는 `Verified Against` 없이 내용 합의로 Active가 된다.

`Verified Against`는 상태값이 아니라 코드 대조 검증 기록이다. Active 문서라도 코드가 바뀌어 검증이 낡으면 `Verified Against`를 재검증 후 갱신하며, 이때 `Status`는 그대로 유지한다.

## Parallel Branch Convergence

- `Current`와 Current table inventory는 최신 dev에 병합된 schema·실행 경로만 뜻한다. 별도 브랜치의 구현은 필요한 경우 `Pending Merge` 또는 Target으로 표시하고, 병합 전 `Verified Against` 기준을 선행 변경하지 않는다.
- ADR, feature requirement, test case의 순번은 병렬 브랜치에서 예약되지 않는다. Merge 또는 rebase 직전에 같은 문서의 최신 dev와 대조해 중복 번호를 재배정하고 모든 내부 링크·표·테스트 참조를 함께 갱신한다.
- 미병합 ADR을 current 상위 문서의 authority로 링크하지 않는다. 병합 시 ADR 번호 충돌, 상태, 인덱스 등록과 관련 feature 문서 반영을 한 번에 확인한다.
- 병렬 구현이 같은 책임을 다른 table/job/outbox로 정의하면 이름을 합치는 대신 business source of truth, execution job과 physical cleanup intent를 먼저 구분한다.

## Archive Boundary

`docs_old/` is a historical archive only. It may contain preserved metadata such as `Source of Truth: Yes` from before the documentation restructure, but that metadata is not current authority.
