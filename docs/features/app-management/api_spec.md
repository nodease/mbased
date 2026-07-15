# App Management API Spec

Status: Draft
Verified Against: feature/mba-87 @ 5c850b2e37d3b29476d8427884cca0ca4345f05d

## Endpoints

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/apps` | 현재 사용자가 접근할 수 있는 App 목록 | authenticated organization member |
| GET | `/api/v1/apps/operations` | 내 모듈 운영 현황 목록 | organization manager or workflow builder/manager |
| DELETE | `/api/v1/apps/{app_id}` | App과 활성 Workflow 설정 삭제 | organization manager or primary workflow manager |

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
      "projected_month_cost": 24.68,
      "previous_month_cost": 10.0,
      "trend_percent": 146.8
    }
  },
  "deployment": { "...": "..." },
  "latest_run": { "...": "..." }
}
```

- `operation_metrics`는 `/dashboard/mymodule` 비용/추세 UI 전용 요약이다.
- `current_month_cost`: 현재 KST 월의 `llm_usage_logs.total_cost` 합계.
- `projected_month_cost`: 현재 월 경과 비율을 기준으로 단순 projection한 월 예상 비용. 계산할 수 없으면 null이다.
- `previous_month_cost`: 직전 KST 월의 `llm_usage_logs.total_cost` 합계.
- `trend_percent`: `projected_month_cost`와 `previous_month_cost`의 증감률. 직전 월 비용이 0이면 null이다.
- `operation_metrics`는 `budget_status`와 별도 필드이며 `GET /apps` 응답에는 포함하지 않는다.

### DELETE /apps/{app_id}

Request body는 없고 `X-Organization-Id: <organization-uuid>` header가 필수다. Gateway는 active organization에서 App을 찾고 manage 권한, 연결 Workflow 무결성, 진행 중 operation을 확인한 뒤 하나의 transaction으로 삭제한다. 성공 응답은 기존 client 호환 shape를 유지한다.

```json
{
  "message": "App deleted successfully"
}
```

성공 시 HTTP `200`이다. App/Workflow와 활성 편집·배포 설정은 hard delete하지만 run·trace·usage·audit·Cost Optimizer·model routing history·Mail/external effect/schedule claim은 retention 정책대로 남긴다. 다른 Workflow graph의 WorkflowNode reference는 수정하지 않는다.

성공 transaction에는 canonical `app.delete`와 삭제된 `user_workflow_permission.deleted`/`team_workflow_permission.deleted` audit row를 함께 기록한다. `app.delete` metadata는 검증된 organization/actor/App/Workflow ID와 대상별 정리 개수만 포함한다. permission audit는 permission·organization·Workflow·user 또는 team ID만 기록하며 graph, secret, credential, raw payload는 기록하지 않는다. 삭제 또는 audit flush가 실패하면 resource와 audit row를 모두 rollback한다.

오류는 공통 safe envelope를 사용한다.

```json
{
  "error": {
    "code": "app.delete_in_progress",
    "message": "App deletion is blocked by an active operation.",
    "request_id": "<opaque-request-id>",
    "details": {}
  }
}
```

| Status | Code | Condition |
| --- | --- | --- |
| 400 | `organization.required` | `X-Organization-Id` 누락 |
| 401 | `auth.required` | 인증 없음 |
| 403 | `permission.denied` | active organization 안의 App이지만 manage 권한 없음 |
| 404 | `resource.not_found` | App 없음, organization scope 밖, 이미 삭제됨 |
| 409 | `app.delete_in_progress` | run/schedule/Mail/external effect가 진행 중이거나 결과 불명 |
| 409 | `app.delete_requires_repair` | 연결 Workflow 100개 초과 또는 organization/연결 무결성 손상 |
| 422 | `validation.failed` | `X-Organization-Id`가 UUID가 아님 |

예상하지 못한 DB 오류는 전체 transaction을 rollback하고 기존 generic `500` envelope로 반환한다. SQL, FK 이름, 내부 row 정보는 응답에 넣지 않는다.

## Errors

- `401`: 미인증
- `403`: active organization 안에서 필요한 App action 권한 없음
- `404`: 직접 조회 대상이 없거나 organization scope 밖 리소스
- `409`: App 삭제와 경합하는 operation 또는 수동 복구가 필요한 legacy 연결

## Permissions

- App 목록은 active organization context를 기준으로 사용자가 읽을 수 있는 App/Workflow만 반환한다.
- 운영 현황은 active organization context를 기준으로 organization manager이거나 workflow `write` 이상 권한을 가진 App/Workflow만 반환한다. Workflow `execute` 전용 사용자는 `/apps/operations` 대상이 아니며, 배포된 챗봇 링크 또는 내부 실행 링크(`/modules/{workflow_id}/run?deploymentId={deployment_id}`)를 사용한다.
- `budget_status`는 사용률과 상태만 노출한다. `/apps/operations`의 `operation_metrics`는 운영 표면에 반환된 workflow row의 비용 요약으로만 사용한다.
- App 삭제는 active organization manager 또는 App primary Workflow의 `manager`만 수행한다. 잠금 뒤 같은 권한을 다시 확인하며, scope 밖 App은 존재 여부를 숨겨 `404`로 반환한다.
