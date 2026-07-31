# 문서 권위

Status: Draft
Authority: Foundation
Source of Truth: Yes
Verified Against: dev @ 860ece0dee7cab3925d27f30ea650baf0cb18b4e (PR #138 docs target, 2026-07-01 KST)

## 목적

문서 간 충돌이 발생했을 때 어떤 문서를 기준으로 판단할지 정의한다.

## 권위 순서

| 충돌 주제 | 기준 또는 처리 문서 |
| --- | --- |
| 용어, 문서 상태, source-of-truth 규칙 | `foundation/*` |
| 제품 요구사항, MVP 완료 기준, 제외 범위 | `requirements/*` |
| 서비스 경계, 레이어, 런타임 흐름, 보안 경계 | `architecture/*` |
| 물리 데이터 모델, 테이블, 권한 정책, migration | `data-model/*` |
| HTTP API, request/response, error contract | `api/*` |
| 프론트 화면, 상태, 컴포넌트, QA 작업 문서 | `front/*` 비권위 작업 문서. 상위 기준과 충돌하면 수정 대상 |
| 배포 런타임, 환경변수, 헬스체크, ingress, container entrypoint | `docker/**`, `infra/**`, `scripts/**`, root `README.md` |
| 중요 설계 결정의 근거 | `decisions/*` |
| 작업 순서, 이슈 분해, rollout | `implementation-plan/*` |
| 과거 조사, 메모, 폐기 초안 | `references/*` |

## 충돌 규칙

1. 같은 주제를 다루는 문서가 충돌하면 위 표의 권위 영역을 따른다.
2. `front/*` 문서는 `Source of Truth: No`인 프론트 구현 작업 문서다. 요구사항, 아키텍처, 데이터 모델, API, ADR과 충돌하면 `front/*` 문서를 수정한다.
3. `implementation-plan/*` 문서가 요구사항, 아키텍처, 데이터 모델, API 문서와 충돌하면 구현 계획을 수정한다.
4. `references/*` 문서는 구현 기준이 아니다.
5. `Status: Superseded` 문서는 active source of truth가 아니다.
6. 권위 영역을 바꾸는 결정은 ADR로 기록한다.

## 필수 메타데이터

새 active 문서는 가능하면 아래 메타 정보를 문서 상단에 둔다.

```md
Status: Draft | Accepted | Superseded
Authority: Foundation | Requirements | Architecture | Data Model | API | Frontend Implementation Guide | Decision | Implementation Plan | Reference
Source of Truth: Yes | No
Verified Against: dev @ <commit> 또는 <branch> @ <commit> (<date/timezone>)
Related ADRs:
```

## 현재 기준

현재 active 문서 정합성 기준은 각 문서 상단의 `Verified Against`가 가리키는 repository code snapshot이다. 가능하면 재현 가능한 commit hash를 사용한다. 문서-only PR에서 목표 계약을 바꾸는 경우 PR 번호나 target contract 메모를 괄호에 덧붙일 수 있지만, branch의 current snapshot처럼 움직이는 기준을 최종 기준으로 두지 않는다. `현재 코드`, `current behavior`, `Implemented`로 표시한 문장은 해당 snapshot의 실제 코드와 일치해야 한다.

`목표`, `Planned`, `MVP 목표 계약`으로 표시한 요구사항, 아키텍처, 데이터 모델, API, ADR은 코드가 수렴해야 할 계약이다. 코드가 목표 계약과 다르면 목표 문서를 현재 코드로 낮추기보다 구현 보강 이슈, discrepancy report, 또는 단계별 migration 계획으로 추적한다.

`front/*` 비권위 작업 문서 규칙은 초기 프론트 문서 구조 정리 작업에서 추가했다. 이 규칙은 `front/*`를 active source of truth로 승격하지 않고, 상위 기준 문서를 프론트 구현 단위로 번역하는 보조 문서 영역으로만 정의한다.

`references/moduly-architecture/`는 과거 `main` 기준 역공학 문서다. 이 문서는 현재 코드의 ERD나 tracing/RBAC 구조를 완전히 반영하지 않으므로 구현 기준으로 사용하지 않는다.
