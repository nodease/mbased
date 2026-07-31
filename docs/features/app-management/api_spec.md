# App Management API Spec

Status: Draft

## Endpoints

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/apps` | 현재 사용자가 접근할 수 있는 App 목록 | authenticated organization member |
| GET | `/api/v1/apps/operations` | 워크플로우 운영 현황 목록 | organization manager or workflow builder/manager |
| GET | `/api/v1/apps/operations/cost-summary` | 활성 배포 전체 예상 월 비용 요약 (호환 API) | organization manager or workflow builder/manager |

## Request And Response Models

### GET /apps

기존 `AppResponse`는 App 기본 정보, primary `workflow_id`, 활성 배포 요약, owner 표시명을 반환한다.

Budget Management 확장 계약은 [budget-management api_spec](../budget-management/api_spec.md)의 `GET /apps (확장)`을 따른다.

```json
{
  "id": "<uuid>",
  "name": "<string>",
  "workflow_id": "<uuid|null>",
  "budget_status": {
    "usage_ratio": 0.923457,
    "status": "at_risk"
  }
}
```

- `budget_status`는 안전 목록 요약이다. 예산 금액과 당월 비용 원문은 포함하지 않는다.
- 활성 예산이 없거나 `workflow_id`가 null이면 `budget_status`는 null이다.
- 같은 `app_id`에 과거/보조 Workflow row가 남아 있어도 App의 primary workflow(`apps.workflow_id`)가 아니면 `budget_status` 후보로 사용하지 않는다.
- `budget_status` 계산 규칙과 N+1 금지는 [budget-management api_spec](../budget-management/api_spec.md)의 `GET /apps, GET /apps/operations (확장)`을 따른다.
- 당월 비용 합산은 실행 차단과 동일하게 primary workflow id와 KST 월 경계 기준이며, `llm_usage_logs.organization_id`가 NULL인 기존/마이그레이션 usage row도 포함한다.

### GET /apps/operations

`/dashboard/mymodule`의 원천이다. 이 화면은 최종 사용자의 workflow 실행 표면이 아니라 작성자/운영자가 배포, 권한, 비용, 최근 실행 상태를 확인하는 운영 표면이다. 응답 항목의 `app` summary는 App 기본 정보와 운영 상태를 함께 표시하기 위한 안전 요약이다.

Budget Management 확장 시 `app.budget_status`는 `GET /apps`의 `budget_status`와 동일한 shape를 사용한다.

```json
{
  "app": {
    "id": "<uuid>",
    "name": "<string>",
    "workflow_id": "<uuid|null>",
    "budget_status": {
      "usage_ratio": 0.923457,
      "status": "at_risk"
    },
    "operation_metrics": {
      "current_month_cost": 12.34,
      "current_month_workflow_execution_cost": 10.0,
      "current_month_agent_builder_cost": 2.34,
      "projected_month_cost": 24.68,
      "projected_month_workflow_execution_cost": 20.0,
      "projected_month_agent_builder_cost": 4.68,
      "previous_month_cost": 10.0,
      "trend_percent": 146.8,
      "usage_data_complete": false,
      "unresolved_provider_call_count": 1
    }
  },
  "deployment": { "...": "..." },
  "latest_run": { "...": "..." }
}
```

- `operation_metrics`는 `/dashboard/mymodule` 비용/추세 UI 전용 요약이다.
- `current_month_cost`: 현재 KST 월의 operation reference 없는 billable legacy usage와 `succeeded` provider usage ledger operation 합계. Ledger-linked compatibility row는 중복 합산하지 않는다.
- `current_month_workflow_execution_cost`, `current_month_agent_builder_cost`: 현재 월 총비용의 구분값. Agent Builder는 exact `runtime_surface=agent_builder_intent`, workflow 실행은 NULL과 그 밖의 surface다.
- `projected_month_cost`: 현재 월 경과 비율을 기준으로 단순 projection한 월 예상 비용. 계산할 수 없으면 null이다.
- `projected_month_workflow_execution_cost`, `projected_month_agent_builder_cost`: 각 당월 구분값에 총비용과 같은 projection 배수를 적용한 값이다. 두 값의 합은 `projected_month_cost`와 같다.
- `previous_month_cost`: 직전 KST 월의 같은 mixed read model 비용 합계.
- `trend_percent`: `projected_month_cost`와 `previous_month_cost`의 증감률. 직전 월 비용이 0이면 null이다.
- `usage_data_complete`, `unresolved_provider_call_count`: 당월 범위에 `provider_started|outcome_unknown` canonical operation이 없을 때만 complete다. 불완전해도 계산 가능한 확정 비용은 반환하며 클라이언트는 미확정 건수를 함께 표시한다.
- 전월 추세와 `budget_status`는 기존 총비용 기준이며 구분별 추세나 별도 Agent Builder 예산은 제공하지 않는다.
- `operation_metrics`는 `budget_status`와 별도 필드이며 `GET /apps` 응답에는 포함하지 않는다.
- `workflow_id`가 null인 legacy App은 기존 row 응답을 유지한다. null이 아닌 primary workflow는 해당 App id와 active organization id를 함께 소유해야 하며, 검증에 실패한 App은 운영 목록에서 제외한다. 다른 workflow의 권한, 최근 실행, 비용을 대체값으로 사용하지 않는다.
- `deployment.state=active`는 `apps.active_deployment_id`가 가리키는 deployment의 `app_id`가 현재 App id와 같고 `is_active=true`일 때만 반환한다. 조건을 만족하지 않는 참조는 해당 App의 배포 이력으로 `inactive` 또는 `undeployed`를 결정하고, `automatic_optimization`은 null이다.

### GET /apps/operations/cost-summary

호환성을 위해 유지하는 전체 합계 API다. 목록의 pagination, 검색, filter와 관계없이 현재 사용자가 운영할 수 있는 활성 배포 App primary workflow 전체를 집계한다. `/dashboard/mymodule`은 상단 요약 카드 제거 후 이 API를 호출하지 않는다 ([ADR-0060](../../decisions/ADR-0060-my-module-cost-summary-presentation.md)).

```json
{
  "active_workflow_count": 101,
  "projected_month_cost": 303.0,
  "projected_month_workflow_execution_cost": 202.0,
  "projected_month_agent_builder_cost": 101.0,
  "usage_data_complete": false,
  "unresolved_provider_call_count": 2
}
```

- 활성 workflow 판정과 접근 권한은 `GET /apps/operations`과 같다.
- primary workflow가 해당 App과 active organization 소유인지 검증할 수 없는 App은 활성 workflow 수와 세 비용 합계에서 제외하며, 다른 workflow로 대체하지 않는다.
- `apps.active_deployment_id`는 application-level 참조이므로, 가리킨 deployment의 `app_id`가 해당 App id와 다르면 활성 배포로 인정하지 않는다.
- 세 비용은 `operation_metrics`와 같은 KST 월 경계와 projection 배수를 사용한다.
- `projected_month_cost = projected_month_workflow_execution_cost + projected_month_agent_builder_cost`를 만족한다.
- `usage_data_complete`는 대상 workflow 전체의 미확정 provider call이 0개일 때만 true이고 `unresolved_provider_call_count`는 그 합계다.
- 기존 `GET /apps/operations`의 배열 response는 호환성을 위해 변경하지 않는다.

## Errors

- `401`: 미인증
- `403`: organization scope 또는 App 접근 권한 없음
- `404`: 직접 조회 대상이 없거나 scope 밖 리소스

## Permissions

- App 목록은 active organization context를 기준으로 사용자가 읽을 수 있는 App/Workflow만 반환한다.
- 운영 현황 목록과 비용 요약은 active organization context를 기준으로 organization manager이거나 workflow `write` 이상 권한을 가진 App/Workflow만 반환한다. Workflow `execute` 전용 사용자는 `/apps/operations`과 `/apps/operations/cost-summary` 대상이 아니며, 배포된 챗봇 링크 또는 내부 실행 링크(`/modules/{workflow_id}/run?deploymentId={deployment_id}`)를 사용한다.
- primary workflow의 App 또는 organization 소유 검증에 실패한 경우에는 organization manager라도 해당 workflow의 운영 정보나 비용을 조회할 수 없다.
- `budget_status`는 사용률과 상태만 노출한다. `/apps/operations`의 `operation_metrics`는 운영 표면에 반환된 workflow row의 비용 요약으로만 사용한다.
