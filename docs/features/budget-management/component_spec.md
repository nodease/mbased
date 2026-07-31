# Budget Management Component Spec

Status: Draft
Verified Against: feature/mba-147 @ e1a04e9

새 화면을 만들지 않고 기존 화면 세 곳을 확장한다. 프론트의 예산 표시/차단은 UX 보조이며 최종 차단은 Gateway가 수행한다 (NFR-001).

## Screens

### `/dashboard/admin` — 비용 탭 (관리자 예산 설정, FR-051)

[admin-dashboard component_spec](../admin-dashboard/component_spec.md)의 비용 탭(`UsageTab`) 테이블을 확장한다.

| 영역 | 내용 | 노출 조건 |
| --- | --- | --- |
| 비용 탭 테이블 예산 컬럼 | workflow별 예산(USD), 당월 사용률, 상태 배지, 예산 설정 버튼 | organization owner/manager |
| 상단 요약 카드 | 기존 `AdminSummaryCards`의 예산 카드가 실제 `budget` 블록 데이터를 표시 | organization owner/manager |

- 데이터 원천: `GET /admin/usage/workflows`의 `budget` 블록, `GET /admin/summary`.
- 비용 탭 row 기준은 organization scope 안의 App primary workflow 전체다. 기간 안에 사용량이 없는 workflow도 표시하고 호출 수/tokens/비용은 0으로 보여준다.
- 예산 컬럼: 활성 예산이 없으면(`budget` null) "미설정"과 예산 설정 버튼만 표시한다.
- 예산 설정 버튼 → `BudgetEditModal` 열림.

### `/dashboard/mymodule` — 내 워크플로우 운영 목록 (FR-052)

기존 워크플로우 운영 목록(`apps/client/app/dashboard/mymodule/page.tsx`, 원천 `GET /apps/operations`)의 각 row를 확장한다.

- 데이터 원천: `GET /apps/operations`의 `row.app.budget_status`. `GET /apps`는 같은 shape를 제공하지만 `/dashboard/mymodule`의 표시 원천은 operations 응답이다.
- 표시 대상: organization manager 또는 workflow `write` 이상 권한을 가진 작성자/운영자. 배포된 workflow를 실행만 하는 일반 사용자는 이 화면 대신 챗봇 링크 또는 내부 실행 링크를 사용한다.
- 리스트 보기에서 `budget_status`가 있으면 사용률(%)과 상태 배지(`BudgetStatusBadge`)를 표시한다. null이면 아무것도 표시하지 않는다 (기존 레이아웃 유지). 그리드 카드는 예산 사용률과 상태 배지를 표시하지 않는다.
- `status`가 `exceeded`면 실행 상태 영역에 "실행 차단" 표시와 "월 예산 초과로 실행이 차단되었습니다" tooltip을 표시한다. 현재 `/dashboard/mymodule`에는 별도 실행 버튼이 없으므로 편집/조회 진입은 차단하지 않는다.
- 운영 요약 표면이므로 예산 금액은 표시하지 않는다 (BGT-REQ-022). 사용률과 상태는 리스트 보기에서만 표시한다.

### Workflow 편집 화면 — 테스트 실행

- 테스트 실행/스트림 요청이 `429 budget.exceeded`로 실패하면 "월 예산 초과로 실행이 차단되었습니다" 안내(toast/배너)를 표시한다. 일반 실행 오류(500 계열)와 구분한다.
- 편집 화면 진입 시점의 사전 차단(버튼 disable)은 선택 사항이다. 최소 요구는 429 응답의 안내 처리다.

## Components

### BudgetEditModal (신규)

- 위치: 비용 탭 예산 설정 버튼에서 열리는 모달. 기존 모달/폼 패턴을 재사용한다.
- 입력: `monthly_budget_usd`(양수, USD, 소수점 2자리, 최대 `9999999999.99`), `is_enabled` 토글.
- 초기값: `GET /admin/workflow-budgets/{workflow_id}` (404면 신규 설정 폼).
- 저장: `PUT /admin/workflow-budgets/{workflow_id}`. 성공 시 비용 탭 테이블과 요약 카드를 refetch한다.
- 검증: 0 이하/비숫자/소수점 3자리 이상/`9999999999.99` 초과 입력은 제출 전에 막고, 서버 422 응답도 필드 오류로 표시한다.

### BudgetStatusBadge (신규, 공용)

- 입력: `status` (`normal` | `at_risk` | `exceeded`), 선택적으로 `usage_ratio`.
- 표시: `normal` 기본색, `at_risk` 경고색(위험), `exceeded` 오류색(초과). 사용률은 % 정수 반올림 표시 (표시 직전 1회 반올림, 판정은 서버 값).
- 관리자 비용 탭과 내 워크플로우 목록에서 공용으로 사용한다.

### UsageTab 예산 컬럼 확장

- 기존 비용 탭 테이블에 컬럼 추가: 예산(USD, 소수점 2자리), 사용률(%), 상태(`BudgetStatusBadge`), 예산 설정 버튼.
- 기간 안에 사용량이 없는 workflow도 row로 남겨 예산 설정 버튼을 제공한다. 사용량 값은 0으로 표시한다.
- `budget` null인 row는 "미설정" 텍스트와 설정 버튼만 표시한다.

## States And Error Handling

- 로딩/빈 목록/오류 상태는 각 화면의 기존 패턴을 따른다.
- 비용 탭의 빈 목록은 organization scope 안에 App primary workflow가 하나도 없을 때만 표시한다. usage row가 없는 workflow는 빈 목록이 아니라 사용량 0 row다.
- `budget`/`budget_status` null은 오류가 아니라 "예산 미설정" 정상 상태다.
- 예산 설정 API의 403(owner/manager 아님)은 안내 문구로 처리한다. UI 노출 제어(비용 탭 자체가 owner/manager 전용)가 선행하지만 서버 응답 처리도 유지한다.
- 429 `budget.exceeded` 처리 후에도 다른 실행 오류 처리(기존 timeout/500 처리)는 그대로 유지한다.

## Conversation Memory Reservation Target

현재 구현의 사후 집계·차단 helper와 별도인 application capability다. Conversation Memory summary 구현 전에 다음 port를 additive로 제공한다.

- `reserve_estimated_cost(provider_execution_capability, billing_scope, idempotency_key, estimate)`
- `commit_actual_usage(reservation_id, idempotency_key, actual_usage)`
- `release_unused_reservation(reservation_id, reason)`
- `reconcile_unknown_outcome(reservation_id, provider_attempt_ref)`

Reservation adapter는 [LLM Credentials API Spec](../llm-credentials/api_spec.md#target-provider-execution-capability-contract)이 소유하는 opaque ProviderExecutionCapability identity/revision과 Budget에 필요한 purpose/pricing/cap/expiry binding 및 server-derived billing principal을 검증한 뒤 provider 호출 전에 durable approval을 반환하고 중복 key에 같은 결과를 재생한다. Memory context lease, provider attempt와 usage reconciliation은 같은 capability identity/revision을 사용한다. Budget은 credential principal이나 permission decision revision을 자체 합성하지 않는다. Memory가 Budget table을 직접 query하거나 reservation 미지원 adapter에서 summary를 실행해서는 안 된다. 일반 workflow 실행 전체의 BGT-REQ-035 overshoot 정책은 이 target extension 때문에 자동 변경되지 않는다.

Query embedding adapter는 ADR-0069의 durable provider operation 경계를 canonical embedding model별로 사용한다. `purpose=query_embedding`, `output_token_cap=0`, stable provider attempt와 pricing revision이 일치할 때만 outbound를 시작하며 raw query/vector를 Budget projection이나 reconciliation payload로 전달하지 않는다. Durable ledger가 구성되지 않은 capability runtime은 legacy usage log로 대체하지 않고 fail-closed한다.

Pricing lookup이 unavailable하거나 estimate가 invalid/unknown-zero이면 `reserve_estimated_cost`는 typed `budget.price_unavailable`을 반환한다. Memory adapter가 0원 또는 임의 보수 가격을 자체 생성하지 않으며 summary provider를 호출하지 않는다.

Execution subject, credential principal, billing principal과 audit actor는 별도 typed input이다. Public Conversation Access Grant는 reservation principal이 아니며 app/deployment creator는 canonical deployment policy가 billing/credential principal로 명시한 경우에만 그 역할로 사용한다.

## 관련 기존 문서

- [admin-dashboard component_spec](../admin-dashboard/component_spec.md): `AdminSummaryCards` 실데이터 표시와 비용 탭 예산 컬럼을 함께 정의한다.
- [app-management component_spec](../app-management/component_spec.md): 내 워크플로우 목록 row의 예산 상태 표시를 함께 정의한다.
