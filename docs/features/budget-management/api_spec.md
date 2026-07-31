# Budget Management API Spec

Status: Draft
Verified Against: feature/mba-147 @ e1a04e9

예산 관리 전용 API는 admin-dashboard와 같은 `/api/v1/admin/*` prefix를 사용한다. 모든 admin endpoint는 인증과 `X-Organization-Id` header를 요구하고, 범위는 해당 organization scope로 제한한다 ([ADR-0009](../../decisions/ADR-0009-active-organization-header-context.md)).

기존 API 확장(`GET /admin/usage/workflows`, `GET /admin/summary`, `GET /apps`, `GET /apps/operations`)과 실행 차단 응답도 이 문서에서 정의한다. [admin-dashboard api_spec](../admin-dashboard/api_spec.md)과 [app-management api_spec](../app-management/api_spec.md)의 해당 응답 정의도 같은 계약을 따른다.

## Endpoints

| Method | Path | Description | Auth |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/workflow-budgets` | workflow 예산 목록 조회 | organization owner/manager |
| GET | `/api/v1/admin/workflow-budgets/{workflow_id}` | workflow 예산 단건 조회 | organization owner/manager |
| PUT | `/api/v1/admin/workflow-budgets/{workflow_id}` | workflow 예산 설정/수정/비활성화 (upsert) | organization owner/manager |

- 설정/수정/비활성화는 workflow당 최대 1개인 예산의 최신 상태 upsert이므로 PUT 하나로 처리한다 (BGT-REQ-002). 비활성화는 `is_enabled=false`로 보낸다. DELETE는 제공하지 않는다.
- 관리자 화면의 예산/사용률 표시 주 원천은 `GET /admin/usage/workflows`의 `budget` 블록이다. 이 endpoint들은 예산 설정값 자체의 조회/변경용이다.

## Request And Response Models

공통 규칙:

- Pagination은 기존 패턴을 따른다: `page`(1-base, 기본 1), `limit`(기본 20, 최대 100), 응답은 `{ "total": <int>, "items": [...] }`.
- 비용/예산 값은 USD이며 JSON number로 반환한다. 반올림은 클라이언트 표시 계층에서 1회만 수행한다. `usage_ratio`와 상태 판정은 반올림 전 값 기준이다 (BGT-REQ-012).
- `status`는 `normal` | `at_risk` | `exceeded` 문자열이다.
- 상태 판정은 반올림 전 `usage_ratio` 기준으로 80% 미만 `normal`, 80% 이상 100% 이하 `at_risk`, 100% 초과 `exceeded`다 (BGT-REQ-012).

### GET /admin/workflow-budgets

Query: `page`, `limit`

Response `200`:

```json
{
  "total": 3,
  "items": [
    {
      "workflow_id": "<uuid>",
      "workflow_name": "<string>",
      "monthly_budget_usd": 100.0,
      "is_enabled": true,
      "created_by": "<uuid|null>",
      "updated_by": "<uuid|null>",
      "created_at": "<datetime>",
      "updated_at": "<datetime>"
    }
  ]
}
```

- 요청 organization scope의 예산 row만 반환한다. 정렬은 `updated_at` 내림차순.
- `workflow_name`은 admin usage API와 동일하게 `workflows.app_id`로 연결된 `apps.name`을 사용한다.

### GET /admin/workflow-budgets/{workflow_id}

Response `200`: 목록 항목과 동일한 필드 + 당월 판정 값.

```json
{
  "workflow_id": "<uuid>",
  "workflow_name": "<string>",
  "monthly_budget_usd": 100.0,
  "is_enabled": true,
  "current_month_cost": 92.345678,
  "usage_ratio": 0.923457,
  "status": "at_risk",
  "usage_data_complete": false,
  "unresolved_provider_call_count": 1,
  "created_by": "<uuid|null>",
  "updated_by": "<uuid|null>",
  "created_at": "<datetime>",
  "updated_at": "<datetime>"
}
```

- `current_month_cost`/`usage_ratio`/`status`는 당월(KST) 기준이다 (BGT-REQ-011).
- `usage_data_complete=false`이면 `unresolved_provider_call_count`가 1 이상이며, `current_month_cost`는 현재 확정 가능한 합계일 뿐 최종 비용이 아니다. 이 상태의 활성 예산 workflow 실행은 BGT-REQ-033에 따라 fail-closed한다.
- 당월 비용은 예산 row의 organization과 같은 usage 또는 NULL legacy usage만 합산한다. 다른 organization UUID가 명시된 usage는 단건 관리자 응답에서 제외한다 (BGT-REQ-024).
- 비활성이거나 예산이 0 이하면 `usage_ratio`와 `status`는 null이다 (BGT-REQ-010).
- 예산이 설정되지 않은 workflow는 `404 resource.not_found`가 아니라 `200`에 예산 필드 null로 반환하지 않고, `404`로 반환한다 — 예산 row가 없는 상태와 조직 scope 밖을 클라이언트가 구분할 필요가 없고, 설정 UI는 목록/usage 응답의 null로 미설정을 판단한다.

### PUT /admin/workflow-budgets/{workflow_id}

Request body:

```json
{ "monthly_budget_usd": 100.0, "is_enabled": true }
```

- `monthly_budget_usd`: 필수, 0보다 큰 number. 소수점 2자리까지 허용하고 `NUMERIC(12,2)` 저장 범위(`9999999999.99` 이하)를 초과하면 `422`로 거부한다.
- `is_enabled`: 필수 boolean.
- Request body의 unknown field는 `422`로 거부한다. 예: `is_enabledd` 같은 오타 필드는 조용히 무시하지 않는다.

Response `200`: 단건 조회와 동일한 shape와 organization-scoped 당월 비용 projection을 사용한다. 신규 생성이어도 `200`으로 통일한다 (BGT-REQ-024).

Side effects:

- row가 없으면 생성하고 `workflow_budget.created`, 있으면 갱신하고 `workflow_budget.updated`를 audit에 기록한다 (BGT-REQ-040). `audit_metadata`는 `monthly_budget_usd`, `is_enabled` 운영 summary로 제한한다.
- `created_by`는 생성 시 1회 기록하고, `updated_by`는 매 갱신 시 기록한다.
- 기존 값과 동일한 no-op PUT은 갱신/audit 없이 현재 상태를 반환한다.
- 동시성 (BGT-REQ-005): row가 없는 상태에서 같은 workflow에 PUT이 경합하면 `UNIQUE(workflow_id)` 제약 위에서 upsert(ON CONFLICT 갱신 또는 IntegrityError 1회 재시도)로 처리한다. row는 1개만 생성되고, 어느 요청도 5xx로 실패하지 않으며, 마지막 커밋이 최종 상태다. 별도 409는 사용하지 않는다 (권한 신청 approve/reject의 상태 전이 경합과 달리 upsert는 멱등 갱신이므로 last-write-wins가 맞다).
- 요청 Workflow는 App lifecycle lock 획득 뒤에도 해당 App의 current primary여야 한다. Primary 전환에 밀렸거나 이미 non-primary인 Workflow이면 `409 workflow.primary_changed`를 반환하고 예산/audit을 변경하지 않는다. 이는 같은 current primary에 대한 두 budget PUT의 last-write-wins 계약과 별개의 stale resource conflict다.

### GET /admin/usage/workflows (확장)

기존 응답 항목([admin-dashboard api_spec](../admin-dashboard/api_spec.md))에 `budget` 블록을 추가한다.

```json
{
  "workflow_id": "<uuid>",
  "workflow_name": "<string>",
  "prompt_tokens": 12345,
  "completion_tokens": 2345,
  "call_count": 87,
  "total_cost": 12.345678,
  "budget": {
    "monthly_budget_usd": 100.0,
    "current_month_cost": 92.345678,
    "usage_ratio": 0.923457,
    "status": "at_risk"
  }
}
```

- 응답 item은 App과 Workflow의 organization이 모두 요청 organization과 일치하는 App primary workflow(`apps.workflow_id`) 전체를 대상으로 한다. App/Workflow organization이 불일치하거나 Workflow row가 없으면 item과 `total`에서 제외한다. 기간 안에 eligible usage row가 없는 workflow도 item으로 반환하며 `prompt_tokens=0`, `completion_tokens=0`, `call_count=0`, `total_cost=0`이다.
- `total`은 기간 안에 usage row가 있는 workflow 수가 아니라 응답 대상 App primary workflow 수다.
- 조회 기간 usage와 `budget.current_month_cost`는 `llm_usage_logs.organization_id`가 요청 organization과 같거나 NULL인 row만 합산한다. 명시적 타 organization row는 관리자 projection에서 제외한다. NULL legacy usage를 포함하는 호환 계약은 유지한다.
- `budget`은 활성 예산(`is_enabled=true` ∧ `monthly_budget_usd > 0`)이 없으면 null이다.
- `budget` 블록은 query의 `startAt`/`endAt` 기간 필터와 무관하게 항상 당월(KST) 기준으로 계산한다 (BGT-REQ-020). `total_cost`는 기존대로 조회 기간 기준이다.
- 활성 예산이 있고 당월 usage row가 없으면 `budget.current_month_cost=0`, `usage_ratio=0`, `status="normal"`이다.

### GET /admin/summary (확장)

`budget` 블록을 실제 예산 데이터로 반환한다 (기존 spec의 "확정 전 null" 단계 종료).

```json
{
  "month": "2026-07",
  "total_cost": 123.456789,
  "budget": {
    "budgeted_workflow_count": 5,
    "at_risk_count": 1,
    "exceeded_count": 1,
    "ratio": 0.4
  }
}
```

- `budgeted_workflow_count`: 조직의 활성 예산 workflow 수 (`ratio`의 분모).
- `ratio` = (`at_risk_count` + `exceeded_count`) / `budgeted_workflow_count`.
- `ratio`는 기존 소비자 호환을 위해 유지한다. 관리 대시보드 카드의 대표 값은 `at_risk_count + exceeded_count`다.
- 활성 예산 workflow가 0개면 `budget`은 null이다 (클라이언트는 "예산 미설정" 표시, BGT-REQ-021).

### GET /apps, GET /apps/operations (확장)

내 워크플로우 목록/운영 현황 원천인 operations row의 `app` summary와 기존 `AppResponse`에 additive 필드 `budget_status`를 추가한다. `/dashboard/mymodule`의 FR-052 예산 상태 표시는 `GET /apps/operations`의 `app.budget_status`를 사용한다. `GET /apps`는 dashboard 홈, 설정, 관리자 보조 화면 등 기존 App 목록 소비자에게 같은 안전 요약을 제공한다.

```json
{
  "...기존 AppResponse 필드...": "...",
  "budget_status": {
    "usage_ratio": 0.923457,
    "status": "at_risk"
  }
}
```

- App의 primary workflow(`apps.workflow_id`) 기준이다. 활성 예산이 없거나 `workflow_id`가 null이면 `budget_status`는 null이다.
- 같은 `app_id`에 연결된 과거/보조 Workflow row는 `budget_status` 후보가 아니다. `apps.workflow_id`가 null이거나 해당 workflow에 활성 예산이 없으면, 다른 Workflow row에 활성 예산이 있어도 `budget_status`는 null이다.
- 안전 요약이므로 예산 금액과 비용 원문은 포함하지 않는다 (BGT-REQ-022~023). `GET /apps/operations` row는 organization manager 또는 workflow `write` 이상 권한을 가진 운영 사용자에게만 반환한다.
- `usage_ratio`와 `status`의 판정은 관리자 예산 블록과 동일하게 당월(KST) 비용 합계와 반올림 전 값을 사용한다.
- 당월 비용 합계는 실행 차단 판정과 동일하게 `workflow_id`와 KST 월 경계 기준으로 계산한다. 예산 row는 organization scope로 조회하지만, 사용량 합산에서는 `llm_usage_logs.organization_id`를 필수 조건으로 요구하지 않는다. `workflow_id`가 일치하는 기존/마이그레이션 로그는 `organization_id`가 NULL이어도 포함한다.
- 목록 전체의 사용률 계산은 현재 응답 App의 primary workflow id를 모아 workflow별 당월 비용을 grouped query로 조회한다 (N+1 금지). 누락 복구를 위해 `workflows.app_id`로 workflow 후보를 확장하지 않는다.
- `GET /apps/operations`에서는 각 row의 `app.budget_status`에 포함한다. `GET /apps`에서는 각 `AppResponse.budget_status`에 포함한다.

## 실행 차단 응답

`exceeded` 상태 workflow의 실행 요청은 아래 경로 모두에서 Celery dispatch 전에 동일한 오류로 차단한다 (BGT-REQ-030, BGT-REQ-031).

| 경로 | Endpoint |
| --- | --- |
| 테스트 실행 | `POST /api/v1/workflows/{workflow_id}/execute`, `POST /api/v1/workflows/{workflow_id}/stream` |
| 배포 실행 (api) | `POST /api/v1/run/{url_slug}` |
| 배포 실행 (app/public) | `POST /api/v1/run-public/{url_slug}` |
| 배포 실행 (webhook) | `POST /api/v1/hooks/{url_slug}` |
| 배포 실행 (schedule) | scheduler dispatch (HTTP 응답 없음 — 차단 시 실행을 생략하고 audit만 기록) |

Response `429`:

```json
{ "detail": { "code": "budget.exceeded", "message": "Workflow monthly budget exceeded." } }
```

- 오류 코드 문자열은 `budget.exceeded`로 고정한다. 메시지에 예산 금액/당월 비용 원문을 포함하지 않는다 (public 경로 노출 방지).
- `403 permission.denied`(권한), `404 resource.not_found`(scope 밖 숨김)와 구분되는 별도 코드다. 예산 차단은 권한 부족이 아니므로 [ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)의 403/404 정책과 섞지 않는다.
- `POST /workflows/{workflow_id}/compare`는 차단 대상이 아니다 (BGT-REQ-032).
- 판정은 매 요청 dispatch 직전에 DB 집계로 수행하고 결과를 캐시하지 않는다. 초과가 usage log에 반영된 이후의 신규 요청은 모든 경로에서 차단된다 (BGT-REQ-034). 동시 dispatch로 인한 한시적 초과는 수용 한계다 (BGT-REQ-035).
- 차단 시 audit `policy.block`(`audit_metadata.reason='budget.exceeded'`, trigger mode 포함)을 기록한다 (BGT-REQ-041).

## Errors

| Status | 조건 |
| --- | --- |
| 400 | `X-Organization-Id` 누락/invalid |
| 401 | 미인증 |
| 403 | organization scope 안이지만 owner/manager 아님 — `permission.denied` audit 기록 ([ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)) |
| 404 | 요청 organization scope 밖 또는 존재하지 않는 `workflow_id`, 예산 미설정 workflow의 단건 조회 — 존재를 숨긴다 (`resource.not_found`, ADR-0010) |
| 422 | request 형식 오류 — `monthly_budget_usd` 누락/0 이하/숫자 아님/소수점 3자리 이상/저장 범위 초과, `is_enabled` 누락, unknown field 포함 |
| 429 | 예산 초과 실행 차단 (`budget.exceeded`) |

## Permissions

| Endpoint | 판정 |
| --- | --- |
| `GET/PUT /admin/workflow-budgets*` | organization owner/manager 전용 |
| 실행 차단 판정 | 기존 실행 경로의 권한 판정 이후, dispatch 직전 service 경계에서 수행 |

- 모든 판정은 Gateway service/helper 경계에서 수행한다 (NFR-001). 프론트 차단은 UX 보조일 뿐이다.
- 예산 차단은 기존 실행 권한 검사(`execute` permission, deployment auth 등)를 대체하지 않는다. 권한 검사를 통과한 요청에만 추가로 적용된다.
