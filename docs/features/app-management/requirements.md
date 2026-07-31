# App Management Requirements

Status: Draft
Related Features: auth, organization, workflow, deployment, budget-management

## Purpose

TBD

이 feature의 범위에는 앱 탐색/마켓플레이스(`dashboard/explore` 화면, `apps.is_market`)와 앱 복제(`apps.forked_from`)를 포함한다. 마켓플레이스 공개 정책 고도화는 PRD 제외 범위이며, 여기서는 이미 구현된 탐색·복제 동작을 다룬다.

## User Stories

- TBD

## Functional Requirements

- APP-REQ-010: `GET /apps`는 active organization context 기준으로 현재 사용자가 읽을 수 있는 App 목록을 반환한다. 각 App은 기본 정보, primary `workflow_id`, 활성 배포 요약, owner 표시명을 포함한다.
- APP-REQ-020: `GET /apps/operations`는 `/dashboard/mymodule`의 원천으로, 현재 사용자가 운영할 수 있는 App/Workflow row를 반환한다. 운영 가능 기준은 organization manager 또는 workflow `write` 이상 권한이다. Workflow `execute` 전용 사용자는 최종 사용자 실행 주체이므로 이 목록에 포함하지 않는다. 각 row는 App summary, workflow permission summary, deployment state, latest run state를 포함한다. `workflow_id`가 null인 legacy App은 기존처럼 row에 남긴다. null이 아닌 primary workflow는 반드시 현재 App id와 active organization id를 함께 소유해야 한다. 이를 만족하지 않는 잘못된 참조는 운영 목록에서 제외하며, 다른 workflow의 권한·최근 실행·비용으로 대체하지 않는다. 활성 배포는 `apps.active_deployment_id`가 가리키는 deployment의 `app_id`가 현재 App id와 같고 `is_active=true`인 경우만 인정한다. 이 조건을 만족하지 않는 잘못된 참조는 해당 App의 실제 배포 이력으로 `inactive` 또는 `undeployed`를 결정하며, 다른 App의 자동 최적화 요약을 반환하지 않는다.
- APP-REQ-030: Budget Management 확장 시 `GET /apps`의 `AppResponse.budget_status`와 `GET /apps/operations`의 `row.app.budget_status`는 같은 안전 요약 shape를 사용한다. 계산 규칙, null 조건, 노출 금지 필드는 [budget-management requirements](../budget-management/requirements.md)의 BGT-REQ-011, BGT-REQ-022~023을 따른다. App의 primary workflow(`apps.workflow_id`)만 기준으로 하며, 같은 `app_id`의 과거/보조 Workflow row는 예산 상태 후보가 아니다. 사용량 합산은 실행 차단 판정과 동일하게 primary workflow id와 KST 월 경계 기준이며, `llm_usage_logs.organization_id`가 NULL인 기존 로그를 제외하지 않는다. `runtime_surface=agent_builder_intent`인 planner/repair 비용도 동일한 primary workflow 예산 사용액에 포함한다.
- APP-REQ-040: `GET /apps/operations`의 `row.app.operation_metrics`는 `/dashboard/mymodule`의 월 예상 비용, 전월 대비 증가 추세, 최적화 권장 표시의 원천이다. 계산은 App의 primary workflow(`apps.workflow_id`)에 연결된 operation reference 없는 billable legacy `llm_usage_logs`와 `succeeded` provider usage ledger operation을 KST 달력 월 기준으로 grouped aggregate 한다. Ledger-linked compatibility row는 중복 합산하지 않고 ADR-0055의 Agent Builder planner/repair legacy usage는 계속 포함한다. 당월 비용은 현재 KST 월 `[start, end)` 비용 합계이며, 월 예상 비용은 당월 경과 비율로 단순 projection 한다. 전월 비용이 0이거나 없으면 `trend_percent`는 null이다.
- APP-REQ-041: `operation_metrics`는 기존 당월·월 예상 총비용 필드를 유지하고 각 기간에 workflow 실행 비용과 Agent Builder 비용을 additive 필드로 반환한다. `runtime_surface=agent_builder_intent`만 Agent Builder로, NULL과 그 밖의 legacy surface 및 canonical ledger usage는 workflow 실행으로 분류한다. `provider_started|outcome_unknown` ledger operation이 당월에 남으면 계산 가능한 확정 비용과 함께 `usage_data_complete=false`, `unresolved_provider_call_count`를 반환하고 `/dashboard/mymodule`은 이를 확정 총액처럼 숨기지 않고 미확정 건수를 표시한다. 각 workflow 비용 칸은 배포 상태와 관계없이 총비용 아래에 두 구분값을 표시한다. 미배포 workflow는 workflow 실행 비용을 `테스트 실행`으로, 배포 이력이 있는 활성·비활성 workflow는 배포 전 테스트 비용을 포함해 `테스트/배포 실행`으로 표시한다. 배포 시 기존 비용을 초기화하지 않으며 전월 추세와 예산 위험은 총비용 기준을 유지한다.
- APP-REQ-042: `GET /apps/operations/cost-summary`는 목록 페이지, 검색, 필터와 무관하게 현재 사용자가 운영할 수 있는 활성 배포 App primary workflow 전체의 월 예상 비용을 반환한다. primary workflow가 현재 App과 active organization 소유인지 확인할 수 없는 App은 비용·활성 workflow 수에서 제외하며 다른 workflow로 대체하지 않는다. 활성 배포는 `apps.active_deployment_id`가 가리킨 deployment의 `app_id`가 해당 App id와 같고 `is_active=true`인 경우만 인정한다. 응답은 활성 workflow 수와 총비용, workflow 실행 비용, Agent Builder 비용 및 합산된 usage completeness를 포함한다. 호환 API는 유지하지만 `/dashboard/mymodule`은 이를 호출하거나 상단 비용·추세·위험 요약 카드를 렌더링하지 않는다. 기존 `GET /apps/operations` 배열 응답은 변경하지 않는다 ([ADR-0060](../../decisions/ADR-0060-my-module-cost-summary-presentation.md)).
- APP-REQ-050: `/dashboard/mymodule`의 운영 현황은 리스트 보기와 그리드 보기를 제공한다. 두 보기는 같은 operations row, 검색, 필터, 권한 조건과 작업을 사용하며, 그리드 카드는 App/Workflow 이름을 카드의 가장 두드러진 제목으로 표시한다. 사용자가 선택한 보기 방식은 같은 브라우저에서 다시 방문할 때 유지한다.

## Policies And Edge Cases

- `budget_status`는 사용률과 상태만 포함한다. 예산 금액, 당월 비용 원문, credential, raw payload, secret 값은 App Management 응답에 포함하지 않는다.
- `operation_metrics`는 `/apps/operations` 전용 운영 지표다. `budget_status` shape를 확장하지 않으며, `GET /apps` 응답에는 포함하지 않는다.
- `/dashboard/mymodule`은 운영 표면이다. 일반 사원처럼 배포된 workflow를 실행만 하는 사용자는 챗봇 배포 링크나 내부 실행 링크를 사용하며, 비용/최근 실행/최적화 같은 운영 지표를 보지 않는다.
- `budget_status`가 null이어도 App 접근 권한, 배포 상태, 실행 상태의 기존 응답 의미는 바뀌지 않는다.
- App의 `workflow_id`가 null이거나 primary workflow에 활성 예산이 없으면 `budget_status`는 null이다. 같은 `app_id`의 다른 Workflow row에 활성 예산이 있어도 이를 대체값으로 사용하지 않는다.
- App의 `workflow_id`가 null이거나 해당 workflow에 당월 사용 로그가 없으면 `operation_metrics`는 null 또는 0 비용 지표로 처리하며, 클라이언트는 더미 비용/추세를 만들지 않는다.
- App/operations 목록 조회와 예산 수정·비활성화·삭제가 경합해도 목록 API는 5xx 없이 완료되어야 한다. 예산 상태는 같은 조회 스냅샷 기준으로 일관되게 계산하고, 경합 결과 활성 예산을 찾을 수 없으면 null로 반환한다.

## Open Questions

- TBD
