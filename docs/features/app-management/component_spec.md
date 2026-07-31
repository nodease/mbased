# App Management Component Spec

Status: Draft

## Screens

### `/dashboard/mymodule` — 워크플로우 운영 현황

내가 운영할 수 있는 App/Workflow의 권한, 배포 상태, 최근 실행 상태를 표시한다. 목록 데이터 원천은 `GET /apps/operations`다. 이 화면은 작성자/관리자 운영 표면이며, 배포된 workflow를 실행만 하는 일반 사용자의 최종 실행 화면이 아니다.

- 사이드바 navigation과 페이지 제목은 `워크플로우 목록`으로 표시하고, 페이지 제목 왼쪽에는 사이드바와 같은 `Workflow` 아이콘을 표시한다.
- 생성 action은 `새 워크플로우`로 표시하고 기존 CreateAppModal을 연다.

Budget Management 확장:

- 리스트 row의 `app.budget_status`가 있으면 모듈명/설명 아래에 `BudgetStatusBadge`를 표시한다. 그리드 카드는 예산 사용률을 표시하지 않는다.
- `budget_status.status`가 `exceeded`면 실행 상태 영역에 "실행 차단" 표시를 추가하고, title/tooltip 문구는 "월 예산 초과로 실행이 차단되었습니다"를 사용한다.
- `budget_status`가 null이면 기존 row 레이아웃을 유지하고 예산 관련 텍스트를 표시하지 않는다.
- row의 `app.operation_metrics`가 있으면 월 예상 비용, 전월 대비 증가 추세, 최적화 권장 판단의 원천으로 사용한다. 월 예상 총비용 아래에는 `워크플로 실행`과 `Agent Builder` 예상 비용을 함께 표시한다.
- `operation_metrics.usage_data_complete=false`이면 계산 가능한 비용과 함께 `미확정 N건`을 표시한다. 미확정 provider call을 0원 확정 또는 완결된 월 예상 비용으로 숨기지 않는다.
- 상단의 `예상 월 비용`, `평균 증가 추세`, `예산 위험`, `비용 위험 신호` 요약 카드는 렌더링하지 않는다. 화면 진입과 새로고침에서 `GET /apps/operations/cost-summary`를 호출하지 않는다 ([ADR-0060](../../decisions/ADR-0060-my-module-cost-summary-presentation.md)).
- `operation_metrics`가 null이거나 `trend_percent`가 null이면 클라이언트는 더미 비용/추세를 만들지 않고 "운영 비용 없음" 또는 "비교 데이터 없음"으로 표시한다.
- `budget_status`는 예산 사용률/상태 전용이고, `operation_metrics`는 `/dashboard/mymodule` 운영 비용 지표 전용이다. 두 필드를 합쳐서 해석하지 않는다.
- 현재 화면의 "열기"는 조회/편집 진입이므로 예산 초과 상태에서도 차단하지 않는다. 실제 실행 차단은 Gateway 실행 경로와 Workflow 편집 화면의 429 처리에서 보장한다.
- 일반 사용자의 실행 흐름은 챗봇 배포 링크 또는 내부 실행 링크(`/modules/{workflow_id}/run?deploymentId={deployment_id}`)를 사용한다. 내부 실행 화면의 뒤로가기는 운영 현황이 아니라 기본 대시보드로 돌아간다.

보기 방식:

- 운영 현황 상단의 `리스트 보기`와 `그리드 보기` 버튼으로 표시 방식을 전환한다. 저장된 선택이 없을 때 기본값은 그리드 보기다.
- 선택한 보기 방식은 브라우저 local storage에 저장하고 다음 방문 때 복원한다.
- 그리드 카드는 App/Workflow 이름을 큰 제목으로 표시하고 설명, 수정 시각, 배포/실행 상태와 실행·열기·앱 설정·배포 상태 작업 버튼만 제공한다. 월 예상 비용, 증가 추세, 예산 사용률, 자동 파라미터 최적화 영역과 본문·하단 작업 영역 사이의 내부 구분선은 표시하지 않는다.
- 보기 방식이 달라도 검색, 필터, 더 보기와 공통 작업의 권한별 disabled 조건·대상은 동일하다. 자동 최적화 관리는 리스트 보기에서만 제공한다.

## Components

- `/dashboard/mymodule` 리스트 보기의 월 예상 비용은 배포 상태와 관계없이 표시한다. 미배포 row는 `테스트 실행`과 `Agent Builder`로, 배포 이력이 있는 활성·비활성 row는 배포 전 비용을 포함한 `테스트/배포 실행`과 `Agent Builder`로 구분한다.

- `BudgetStatusBadge`: Budget Management feature의 공용 배지를 재사용한다.

## States

- 예산 미설정(`budget_status=null`)은 오류가 아니라 정상 상태다.
- `exceeded` 표시는 UX 보조이며, 최종 보안/비용 차단 판단은 Gateway가 수행한다.

## Interactions

- 모듈 row의 열기/앱 설정/배포 상태 변경 동작은 기존 권한 조건을 따른다.
- 예산 상태 표시는 이 상호작용 조건을 바꾸지 않는다.

## Accessibility

- 예산 초과 상태는 색상뿐 아니라 "실행 차단" 텍스트로 표시한다.
- 차단 이유는 title/tooltip으로 제공한다.
- 보기 전환 버튼은 현재 선택 상태를 `aria-pressed`로 전달하고 아이콘과 함께 텍스트 이름을 제공한다.
