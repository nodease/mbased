# Nodease 문서

Status: Draft
Authority: Documentation Index
Source of Truth: Yes
Verified Against: feature/mba-59 @ b92bc9e0f38588495d228fc0d17b10dfaaed03c1

이 디렉터리가 현재 저장소의 active documentation root다. 문서 내부 링크와 권위 규칙은 이 `docs/` 디렉터리를 문서 루트로 간주한다.

## 단일 기준 문서

문서 충돌은 [foundation/document-authority.md](foundation/document-authority.md)를 따른다.

| 영역 | 기준 문서 |
| --- | --- |
| 문서 권위, 용어, 범위 | [foundation/](foundation/README.md) |
| MVP 요구사항, 완료 기준, 제외 범위 | [requirements/](requirements/README.md) |
| 서비스 경계, 런타임, 보안/RBAC 구조 | [architecture/](architecture/README.md) |
| 물리 데이터 모델, 권한 정책, migration 정책 | [data-model/](data-model/README.md) |
| API 계약 | [api/](api/README.md) |
| 프론트 화면, 상태, API 연동 작업 문서 | [front/](front/README.md) |
| 비가역적/중요 설계 결정 | [decisions/](decisions/README.md) |
| 구현 순서, 이슈 분해, 검증 계획 | [implementation-plan/](implementation-plan/README.md) |
| 과거 조사, 메모, 폐기 문서 | [references/](references/README.md) |

## 활성 문서

`Source of Truth: Yes`인 active 문서는 아래와 같다. 세부 충돌은 [foundation/document-authority.md](foundation/document-authority.md)의 권위 순서를 따른다.

| 영역 | Active source-of-truth 문서 |
| --- | --- |
| Foundation | [foundation/document-authority.md](foundation/document-authority.md), [foundation/product-scope.md](foundation/product-scope.md), [foundation/glossary.md](foundation/glossary.md) |
| Requirements | [requirements/overview.md](requirements/overview.md), [requirements/mvp-1-foundation-llmops.md](requirements/mvp-1-foundation-llmops.md), [requirements/mvp-2-governance-rag-audit.md](requirements/mvp-2-governance-rag-audit.md), [requirements/mvp-3-enterprise-ops.md](requirements/mvp-3-enterprise-ops.md) |
| Architecture | [architecture/system-overview.md](architecture/system-overview.md), [architecture/auth-rbac.md](architecture/auth-rbac.md), [architecture/tracing-audit.md](architecture/tracing-audit.md) |
| Data Model | [data-model/physical-data-model.md](data-model/physical-data-model.md), [data-model/rbac-permission-policy.md](data-model/rbac-permission-policy.md) |
| API | [api/README.md](api/README.md), [api/auth.md](api/auth.md), [api/organization-rbac.md](api/organization-rbac.md), [api/apps-workflows.md](api/apps-workflows.md), [api/llm-credentials.md](api/llm-credentials.md), [api/knowledge-rag.md](api/knowledge-rag.md), [api/tracing-audit.md](api/tracing-audit.md), [api/deployments.md](api/deployments.md), [api/supporting-endpoints.md](api/supporting-endpoints.md), [api/errors.md](api/errors.md) |
| Decisions | [decisions/README.md](decisions/README.md) 및 `Status: Accepted` ADR |
| Implementation Plan | [implementation-plan/mvp-1-development-issue-plan.md](implementation-plan/mvp-1-development-issue-plan.md), [implementation-plan/risk-consistency-verification.md](implementation-plan/risk-consistency-verification.md) |

## 참조 문서

참조 문서는 구현 기준이 아니다. 과거 분석이나 아이디어의 출처로만 사용한다.

- [references/moduly-architecture/](references/moduly-architecture/README.md): 과거 Moduly 역공학 참고 문서
- 삭제된 로컬 메모와 폐기 초안은 구현 기준이 아니다.

## 작성 규칙

- 한 문서는 하나의 권위 영역만 다룬다.
- 요구사항, 아키텍처, 데이터 모델, API 계약, 구현 계획을 한 파일에 섞지 않는다.
- 중요한 설계 변경은 [decisions/](decisions/README.md)에 ADR로 남긴다.
- `references/` 아래 문서는 active source of truth가 아니다.
- 문서 내부 링크는 이 문서 루트를 기준으로 상대 경로를 사용한다.
