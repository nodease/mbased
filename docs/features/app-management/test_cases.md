# App Management Test Cases

Status: Draft
Verified Against: feature/mba-87 @ 5c850b2e37d3b29476d8427884cca0ca4345f05d

## Acceptance Criteria

### AC-1. App 목록 예산 상태 (APP-REQ-010, APP-REQ-030)

- Given 사용자가 읽을 수 있는 App의 primary workflow에 활성 예산이 있고 당월 비용이 기록되어 있다, When `GET /apps`를 호출한다, Then 해당 App의 `budget_status`는 `usage_ratio`와 `status`만 포함한다.
- Given 활성 예산이 없거나 App의 `workflow_id`가 null이다, When `GET /apps`를 호출한다, Then 해당 App의 `budget_status`는 null이고 기존 App 필드는 유지된다.
- Given App의 primary workflow에는 활성 예산이 없고 같은 `app_id`의 과거/보조 workflow에는 활성 예산이 있다, When `GET /apps`를 호출한다, Then 해당 App의 `budget_status`는 null이고 보조 workflow 상태를 표시하지 않는다.
- Given 사용자가 읽을 수 없는 App/Workflow가 있다, When `GET /apps`를 호출한다, Then 해당 리소스와 예산 상태는 응답에 포함되지 않는다.

### AC-2. 운영 현황 예산 상태 (APP-REQ-020, APP-REQ-030)

- Given `/dashboard/mymodule`에 표시되는 App row의 primary workflow에 활성 예산이 있다, When `GET /apps/operations`를 호출한다, Then `row.app.budget_status`는 `GET /apps`와 동일한 shape로 반환된다.
- Given `/dashboard/mymodule`에 표시되는 App row의 primary workflow에 당월/전월 `llm_usage_logs` 비용이 있다, When `GET /apps/operations`를 호출한다, Then `row.app.operation_metrics`는 당월 비용, 월 예상 비용, 전월 비용, 전월 대비 증감률을 반환한다.
- Given 전월 비용이 0이거나 없다, When `GET /apps/operations`를 호출한다, Then `row.app.operation_metrics.trend_percent`는 null이고 클라이언트는 더미 증가율을 만들지 않는다.
- Given `/dashboard/mymodule`에 표시되는 App row의 `workflow_id`가 null이고 같은 `app_id`의 과거/보조 workflow에 활성 예산이 있다, When `GET /apps/operations`를 호출한다, Then `row.app.budget_status`는 null이다.
- Given `row.app.budget_status.status`가 `exceeded`다, When 클라이언트가 `/dashboard/mymodule`을 렌더링한다, Then row는 예산 상태 badge와 "실행 차단" 표시를 보여준다.
- Given `row.app.budget_status`가 null이다, When 클라이언트가 `/dashboard/mymodule`을 렌더링한다, Then 예산 관련 텍스트 없이 기존 row 레이아웃을 유지한다.
- Given `row.app.operation_metrics`가 null이다, When 클라이언트가 `/dashboard/mymodule`을 렌더링한다, Then 월 예상 비용/증가 추세/최적화 권장 UI는 "운영 비용 없음" 또는 "비교 데이터 없음"을 표시하고 deterministic dummy 값을 생성하지 않는다.

### AC-3. 안전 요약 노출 제한

- Given `GET /apps` 또는 `GET /apps/operations` 응답을 확인한다, Then `budget_status`에는 `usage_ratio`와 `status`만 포함되고 `monthly_budget_usd`, `current_month_cost`, credential, raw payload, secret 값은 포함되지 않는다.
- Given workflow `execute` 전용 일반 사용자가 있다, When `GET /apps/operations`를 호출한다, Then 해당 workflow row와 운영 비용/최근 실행/최적화 지표는 응답에 포함되지 않는다.
- Given 같은 사용자가 배포 내부 실행 링크(`/modules/{workflow_id}/run?deploymentId={deployment_id}`)를 사용한다, When workflow 실행을 요청한다, Then workflow `execute` 권한과 RAG 실행 주체 권한으로 실행 가능 여부를 판단한다.

### AC-4. 조회 성능과 동시성

- Given 여러 App row가 같은 응답에 포함된다, When `budget_status`를 계산한다, Then 응답 대상 workflow id를 모아 grouped query로 계산하고 App row마다 개별 비용 집계를 반복하지 않는다.
- Given App의 primary workflow usage 중 `llm_usage_logs.organization_id`가 NULL인 기존/마이그레이션 row가 있다, When `GET /apps` 또는 `GET /apps/operations`의 `budget_status`를 계산한다, Then 해당 비용도 합산해 실행 차단 판정과 같은 상태를 반환한다.
- Given 예산 수정/비활성화와 `GET /apps` 또는 `GET /apps/operations` 조회가 동시에 발생한다, When 응답을 생성한다, Then 요청은 5xx 없이 완료되고 각 row의 `usage_ratio`와 `status`는 같은 DB 조회 스냅샷 기준으로 일관된다.
- Given 조회 도중 App의 primary workflow 또는 예산 row가 삭제된다, When 응답을 생성한다, Then 이미 응답 대상인 App은 기존 접근 정책을 유지하고 예산 상태를 계산할 수 없으면 `budget_status=null`로 처리한다.

### AC-5. 예산 상태 경계값

- Given 예산 100 USD와 당월 비용 79.99 USD인 App, When `GET /apps` 또는 `GET /apps/operations`를 호출한다, Then `budget_status.status`는 `normal`이고 `usage_ratio`는 0.7999다.
- Given 예산 100 USD와 당월 비용 80.00 USD인 App, When 조회한다, Then `status`는 `at_risk`이고 `usage_ratio`는 0.8다.
- Given 예산 100 USD와 당월 비용 100.00 USD인 App, When 조회한다, Then `status`는 `at_risk`이고 실행 차단 표시는 표시하지 않는다.
- Given 예산 100 USD와 당월 비용 100.000001 USD인 App, When 조회한다, Then `status`는 `exceeded`이고 `/dashboard/mymodule`은 "실행 차단" 표시를 보여준다.
- Given `total_cost`가 NULL인 usage row만 있는 App, When 조회한다, Then 비용은 0으로 합산되어 `usage_ratio=0`, `status=normal`이다.
- Given KST 월 경계의 usage row가 있다, When KST 7월 기준 조회한다, Then KST 7월 1일 00:00:00 row는 포함하고 KST 8월 1일 00:00:00 row는 제외한다.

### AC-6. 기본 App 삭제 (APP-REQ-050~052)

- Given 기본 App 생성으로 primary Workflow와 creator manager permission이 함께 저장됐다, When creator가 App을 삭제한다, Then FK 500 없이 `200`을 반환하고 App, Workflow, permission, budget, deployment/schedule, active routing policy, App-owned LLM node version이 모두 삭제된다.
- Given 삭제 중 하나의 cleanup 또는 audit 기록이 실패한다, When transaction이 끝난다, Then App과 모든 연결 row는 삭제 전 상태로 rollback된다.
- Given App 삭제 두 요청이 경합한다, When 같은 App row를 잠근다, Then 한 요청만 `200`이고 다른 요청은 commit 뒤 `404 resource.not_found`이며 부분 삭제나 500이 없다.
- Given 새 run/schedule/Mail admission과 DELETE가 경합한다, When 양쪽이 같은 App→Workflow lifecycle lock 순서를 사용한다, Then admission이 먼저면 DELETE는 `409`, DELETE가 먼저면 admission은 deleted target으로 fail-closed한다.

### AC-7. 운영 이력 보존 (APP-REQ-051)

- Given 완료된 run·node run·trace와 LLM usage가 있다, When App을 삭제한다, Then 이력 row와 원래 app/workflow/deployment ID는 그대로 남는다.
- Given Cost Optimizer와 model routing history가 있다, When App을 삭제한다, Then experiment/candidate/verification/update/run/observation/cohort/evidence/validation 이력은 남고 active routing policy만 삭제된다.
- Given 완료된 Mail processing/draft effect, external effect attempt, schedule claim, audit가 있다, When App을 삭제한다, Then 각 row는 retention 정책대로 남고 App/Workflow lifecycle FK가 삭제를 전파하지 않는다.
- Given Agent Builder session/draft가 App/Workflow를 참조한다, When App을 삭제한다, Then session/draft는 자체 만료를 유지하고 reference만 null로 분리된다.

### AC-8. 진행 중 작업 차단 (APP-REQ-053)

- Given `running` WorkflowRun이 있다, When App을 삭제한다, Then `409 app.delete_in_progress`이고 아무 row도 바뀌지 않는다.
- Given `pending|dispatching|enqueued|running` schedule claim 또는 review 전 `execution_outcome_unknown` dead letter가 있다, When 삭제한다, Then 같은 409로 rollback된다.
- Given `prepared|in_flight` external effect 또는 `effect_outcome_unknown + replay_same_key` attempt가 있다, When 삭제한다, Then provider effect 증거를 바꾸지 않고 같은 409를 반환한다.
- Given `pending|processing|ack_pending|outcome_unknown` Mail processing 또는 `pending|claimed|failed_before_effect|outcome_unknown` draft effect가 있다, When 삭제한다, Then 같은 409를 반환한다.

### AC-9. 권한·resource hiding·legacy 연결 (APP-REQ-054~055)

- Given App이 없거나 다른 organization에 있거나 이미 삭제됐다, When DELETE를 호출한다, Then `404 resource.not_found`다.
- Given 같은 organization member지만 organization manager와 primary Workflow manager가 아니다, When DELETE를 호출한다, Then `403 permission.denied`다.
- Given `apps.workflow_id`와 `workflows.app_id`로 찾은 same-organization Workflow가 100개 이하다, When 삭제한다, Then UUID lock 순서로 모두 정리된다.
- Given 연결 Workflow가 100개를 넘거나 cross-organization/mismatched 연결이다, When 삭제한다, Then `409 app.delete_requires_repair`이고 아무 row도 삭제되지 않는다.

### AC-10. 참조 안전성과 audit (APP-REQ-056~057)

- Given 다른 Workflow graph가 삭제 대상 Workflow를 WorkflowNode로 참조한다, When App을 삭제한다, Then 참조 graph는 자동 수정되지 않는다.
- Given 그 Workflow를 preflight한다, When 삭제된 target을 해석한다, Then `workflow_node_target_unavailable`로 차단한다.
- Given 그 Workflow를 실행한다, When 삭제된 target을 해석한다, Then provider effect 전에 `workflow_node.target_unavailable`로 실패한다.
- Given 삭제가 성공한다, Then `app.delete`와 permission delete audit는 같은 transaction에 남고 commit 뒤 success event가 발행된다.
- Given audit metadata와 API 오류를 확인한다, Then secret, credential, graph, raw payload, SQL, FK 이름은 포함되지 않는다.

## Unit Tests

- `AppService.get_user_apps`
  - Given 활성 예산이 있는 App의 primary workflow, When App 목록을 조회하면, Then `AppResponse.budget_status`는 `usage_ratio`와 `status`만 포함한다.
  - Given 예산이 없거나 `workflow_id`가 null인 App, When App 목록을 조회하면, Then `budget_status`는 null이다.
  - Given primary workflow에는 예산이 없고 같은 `app_id`의 보조 workflow에 예산이 있는 App, When 목록을 조회하면, Then 보조 workflow 예산 상태를 `AppResponse.budget_status`로 붙이지 않는다.
  - Given 여러 App을 조회, When `budget_status`를 계산하면, Then primary workflow별 당월 비용은 grouped query로 계산하고 App별 개별 집계 쿼리를 반복하지 않는다.
  - Given primary workflow의 당월 usage 중 `organization_id`가 NULL인 기존 로그가 있다, When `budget_status`를 계산하면, Then 해당 비용도 포함한다.
  - Given 예산 수정/비활성화가 App 목록 조회와 경합한다, When service가 `budget_status`를 붙인다, Then 예외를 전파하지 않고 일관된 before/after 상태 또는 null 중 하나를 반환한다.
  - Given 경계 비용(79.99/80.00/100.00/100.000001, 예산 100), When `budget_status`를 계산하면, Then `normal`/`at_risk`/`at_risk`/`exceeded`를 반환한다.
- `AppService.list_app_operations`
  - Given 활성 예산이 있는 App의 primary workflow, When operations row를 생성하면, Then `row.app.budget_status`는 `GET /apps`와 같은 shape다.
  - Given App의 `workflow_id`가 null이고 같은 `app_id`의 보조 workflow에 예산이 있다, When operations row를 생성하면, Then `row.app.budget_status`는 null이다.
  - Given 안전 요약 응답, Then 예산 금액과 당월 비용 원문은 포함하지 않는다.
  - Given workflow `execute` 전용 사용자가 App을 읽거나 실행할 수 있다, When operations row를 조회하면, Then 해당 App은 운영 현황 목록에서 제외된다.
  - Given workflow `write` 이상 사용자가 App을 조회한다, When operations row를 조회하면, Then 해당 App은 운영 현황 목록에 포함될 수 있다.
  - Given operations page/batch에 여러 App의 primary workflow가 포함된다, When rows를 생성하면, Then primary workflow id 기준 batch 단위 grouped query로 예산 상태를 계산한다.
  - Given primary workflow의 당월 usage 중 `organization_id`가 NULL인 기존 로그가 있다, When rows를 생성하면, Then 해당 비용도 포함해 `row.app.budget_status`를 계산한다.
  - Given KST 월초 직후(예: 2026-07-31 16:00 UTC = 2026-08-01 01:00 KST), When rows를 생성하면, Then 8월 KST 비용 기준으로 `budget_status`를 계산한다.
- App deletion use case
  - 연결 집합 union, 100개 상한, UUID lock 순서, 잠금 뒤 scope/permission 재검사를 검증한다.
  - admission shared lifecycle lock과 삭제 exclusive lock의 양쪽 승자 순서를 검증한다.
  - active/history 분류가 각 repository delete/detach/retain 계획으로 정확히 변환되는지 검증한다.
  - blocker 하나라도 있으면 mutation과 success audit가 모두 없는지 검증한다.
  - cleanup/audit 예외가 전체 UnitOfWork를 rollback하는지 검증한다.

## API Tests

- `GET /api/v1/apps`
  - 활성 예산 workflow가 있는 App은 `budget_status.usage_ratio`와 `budget_status.status`를 반환한다.
  - 예산 미설정 App 또는 `workflow_id=null` App은 `budget_status=null`을 반환한다.
  - 같은 `app_id`의 과거/보조 workflow에 활성 예산이 있어도 primary workflow 예산이 아니면 `budget_status=null`을 반환한다.
  - 응답에는 `monthly_budget_usd`, `current_month_cost`가 포함되지 않는다.
  - 90%, 100%, 100% 초과 경계에서 `status`가 각각 `at_risk`, `at_risk`, `exceeded`로 반환된다.
- `GET /api/v1/apps/operations`
  - `/dashboard/mymodule` row의 `app.budget_status`가 활성 예산 상태를 반환한다.
  - 예산 미설정 row는 기존 운영 현황 필드를 유지하고 `app.budget_status=null`을 반환한다.
  - `row.app.workflow_id`가 null이면 같은 `app_id`의 보조 workflow 예산 상태를 노출하지 않고 `app.budget_status=null`을 반환한다.
  - workflow `execute` 전용 사용자와 권한이 없는 App/Workflow는 목록에서 제외되며, 예산 상태만으로 노출되지 않는다.
  - organization manager 또는 workflow `write` 이상 사용자는 운영 현황 row를 조회할 수 있다.
  - KST 월 경계 row 포함/제외 기준이 `GET /apps`와 동일하다.
- `DELETE /api/v1/apps/{app_id}`
  - 정상 삭제는 `200 {"message":"App deleted successfully"}`다.
  - `X-Organization-Id`가 없으면 `400 organization.required`, UUID가 아니면 `422 validation.failed`다.
  - missing/cross-organization/repeated delete는 `404 resource.not_found`, same-organization 권한 부족은 `403 permission.denied`다.
  - active operation과 legacy repair 조건은 각각 정해진 `409` code이며 DB 내부 정보가 없다.

## E2E Tests

- 빌더가 `/dashboard/mymodule`에 진입하면 예산 위험/초과 workflow row에 `BudgetStatusBadge`가 표시된다.
- 초과 상태 row는 실행 상태 영역에 "실행 차단"을 표시하지만, row 열기/조회 진입은 기존 권한 조건을 따른다.
- 실행 전용 사용자는 사이드바에서 `/dashboard/mymodule` 운영 메뉴를 보지 않고, 내부 실행 화면의 뒤로가기는 기본 대시보드로 이동한다.

## Permission Tests

- `budget_status`는 App/Workflow 목록 또는 운영 현황 권한을 통과한 row에만 붙는다. 권한 없는 workflow와 operations 권한이 없는 execute-only workflow의 예산 상태는 응답에 포함하지 않는다.
- App 삭제는 organization manager 또는 primary Workflow manager만 허용하고, lock 대기 중 권한이 회수되면 잠금 뒤 재검사에서 `403`으로 rollback한다.
- 다른 organization App과 삭제된 App은 권한 row 존재 여부와 관계없이 `404`로 숨긴다.

## Concurrency Tests

- `GET /apps` 조회와 같은 workflow의 `PUT /admin/workflow-budgets/{workflow_id}`가 경합해도 `GET /apps`는 5xx를 반환하지 않는다. 응답은 수정 전 또는 수정 후 중 하나의 일관된 `budget_status`를 반환할 수 있다.
- `GET /apps/operations` 조회와 예산 비활성화가 경합하면 row 자체는 기존 App/Workflow 접근 정책대로 유지하고, 예산 상태는 수정 전 값 또는 null 중 하나로 반환한다.
- 동시에 여러 사용자가 `GET /apps/operations`를 호출해도 예산 조회는 read-only이며 budget/audit row를 생성하거나 갱신하지 않는다.
- 같은 App을 동시에 DELETE하면 PostgreSQL row lock으로 직렬화되어 성공 1건과 `404` 1건만 발생한다.
- DELETE가 진행 중 run/schedule/Mail/external effect 상태 전이와 경합하면 잠금·재검사 시점의 일관된 상태로 전체 성공 또는 `409` 전체 rollback 중 하나만 된다.
- 삭제가 먼저 resource lock을 얻은 뒤에는 새 run/schedule/Mail admission이 provenance ID만으로 active row를 만들지 못한다.
- PostgreSQL migration 검증은 permission이 있는 기본 App 삭제가 FK 오류 없이 끝나고, retained history의 resource ID가 변하지 않으며, migration downgrade/rollback 경계가 문서화됐는지 확인한다.

## Edge Cases

- 당월 비용이 없으면 활성 예산 workflow의 `usage_ratio`는 0이고 `status`는 `normal`이다.
- 같은 primary workflow의 당월 usage row는 `llm_usage_logs.organization_id`가 NULL이어도 `budget_status` 비용 합산에 포함한다.
- 비활성 예산은 `budget_status=null`로 취급한다.
- 예산 row는 활성화되어 있으나 관련 workflow가 응답 대상 App의 `workflow_id`와 연결되지 않으면 `budget_status=null`이다.
- 같은 `app_id`의 과거/보조 Workflow row에 활성 예산이 있어도 App의 primary `workflow_id`와 다르면 `budget_status`에는 반영하지 않는다.
- `monthly_budget_usd`가 0 이하인 비정상 row가 기존 데이터에 남아 있어도 안전 요약에서는 `budget_status=null`로 취급한다.
- KST 월초 직후에도 `budget_status`는 현재 KST 달력 월 기준으로 계산한다.
- primary Workflow가 없지만 `workflows.app_id` legacy child가 있는 App도 연결 집합 규칙으로 정리한다.
- 보존 대상에 명시적 purge 기간이 없어도 App 삭제에서 임의 삭제하거나 null 처리하지 않는다.
- 다른 Workflow graph의 stale target은 저장 데이터 자동 수정 없이 preflight/runtime에서만 안전하게 차단한다.
