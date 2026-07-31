# Budget Management Test Cases

Status: Draft
Verified Against: feature/mba-147 @ e1a04e9

[requirements.md](requirements.md)의 BGT-REQ와 [api_spec.md](api_spec.md), [component_spec.md](component_spec.md)를 검증한다. admin summary/usage 응답의 기존 필드 검증은 [admin-dashboard test_cases](../admin-dashboard/test_cases.md)가 담당하고, 여기서는 예산 필드와 차단 동작을 다룬다.

## Acceptance Criteria

### AC-1. 예산 설정/수정/비활성화 권한 (BGT-REQ-001~003)

- Given 조직 A의 owner/manager, When `PUT /admin/workflow-budgets/{workflow_id}`로 예산을 설정하면, Then 200과 함께 예산 row가 생성되고 `workflow_budget.created` audit이 기록된다.
- Given 기존 예산이 있는 workflow, When 금액을 수정하면, Then 갱신되고 `workflow_budget.updated` audit이 기록되며 `updated_by`가 요청자로 바뀐다.
- Given 기존 예산, When `is_enabled=false`로 비활성화하면, Then row는 삭제되지 않고 `workflow_budget.updated` audit(`audit_metadata.is_enabled=false`)이 기록된다.
- Given 조직 A의 owner/manager가 아닌 member(builder 포함), When 예산 조회/설정 API를 호출하면, Then `403`과 `permission.denied` audit이 기록되고 예산은 변경되지 않는다.
- Given 기존 값과 동일한 no-op PUT, When 호출하면, Then 200이지만 audit이 기록되지 않는다.
- Given `monthly_budget_usd`가 `NUMERIC(12,2)` 저장 범위의 최대값 `9999999999.99`를 초과한다, When `PUT /admin/workflow-budgets/{workflow_id}`를 호출하면, Then request validation 단계에서 `422`로 거부되고 service/DB commit까지 전달되지 않으며 예산 row와 audit은 변경되지 않는다.
- Given `PUT /admin/workflow-budgets/{workflow_id}` request body에 `is_enabledd` 같은 unknown field가 포함된다, When 호출하면, Then request validation 단계에서 `422`로 거부되고 오타 필드는 조용히 무시되지 않으며 service/DB commit까지 전달되지 않는다.

### AC-2. Organization scope 경계 (BGT-REQ-004)

- Given 조직 B 소속 workflow, When 조직 A의 owner/manager가 조직 A 헤더로 그 workflow의 예산을 조회/설정하면, Then `404 resource.not_found`로 숨겨지고 audit은 기록되지 않는다 ([ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)).
- Given 조직 A와 B 각각의 예산, When `GET /admin/workflow-budgets`를 조직 A로 호출하면, Then 조직 A scope의 row만 반환된다.

### AC-3. 사용률 판정 경계 (BGT-REQ-010~012)

- Given 예산 100 USD와 당월 비용 79.99 USD, When 상태를 판정하면, Then `normal`이다.
- Given 당월 비용 80.00 USD (정확히 80%), Then `at_risk`다.
- Given 당월 비용 100.00 USD (정확히 100%), Then `at_risk`이고 실행은 차단되지 않는다.
- Given 당월 비용 100.000001 USD (100% 초과), Then `exceeded`다. 판정은 반올림 전 값 기준이다.
- Given `is_enabled=false` 또는 `monthly_budget_usd <= 0`인 예산, When 판정하면, Then 상태/사용률은 null이고 위험/초과 집계와 차단 대상에서 제외된다.
- Given `total_cost`가 NULL인 usage row, When 당월 비용을 합산하면, Then 0으로 합산된다.
- Given KST 월 경계 근처의 usage row (예: KST 7월 1일 00:30 = UTC 6월 30일 15:30 저장), When 7월 사용률을 계산하면, Then 해당 row는 7월 집계에 포함된다.

### AC-4. 사용률 조회 응답 (BGT-REQ-020~024)

- Given 조직 A에 App primary workflow가 여러 개 있다, When `GET /admin/usage/workflows`를 호출하면, Then 기간 안의 usage 존재 여부와 무관하게 조직 A의 App primary workflow 전체가 반환된다.
- Given 기간 안에 usage row가 없는 workflow, When `GET /admin/usage/workflows`를 호출하면, Then 해당 workflow는 응답에 포함되고 prompt/completion tokens, `call_count`, `total_cost`는 모두 0이다.
- Given 활성 예산 workflow, When `GET /admin/usage/workflows`를 호출하면, Then 항목의 `budget` 블록에 `monthly_budget_usd`, `current_month_cost`, `usage_ratio`, `status`가 포함된다.
- Given 활성 예산 workflow에 당월 usage row가 없다, When `GET /admin/usage/workflows`를 호출하면, Then `budget.current_month_cost=0`, `budget.usage_ratio=0`, `budget.status="normal"`이다.
- Given 조직 A primary workflow에 조직 B로 명시된 당월 usage와 NULL organization legacy usage가 함께 있다, When 조직 A로 조회하면, Then `budget.current_month_cost`는 조직 B usage를 제외하고 NULL usage를 포함한다.
- Given 조직 A App이 조직 B Workflow를 primary workflow로 가리킨다, When 조직 A로 조회하면, Then 해당 item과 budget 설정 진입점은 반환되지 않는다.
- Given 지난달 기간 필터(`startAt`/`endAt`)로 usage를 조회, When 응답을 확인하면, Then `total_cost`는 지난달 기준이지만 `budget` 블록은 당월(KST) 기준이다.
- Given 예산 미설정 workflow, When usage를 조회하면, Then `budget`은 null이고 오류가 아니다.
- Given 활성 예산 workflow 5개 중 at_risk 1개, exceeded 1개, When `GET /admin/summary`를 호출하면, Then `budget`은 `{budgeted_workflow_count: 5, at_risk_count: 1, exceeded_count: 1, ratio: 0.4}`다.
- Given 활성 예산 workflow는 있지만 모두 normal, When `GET /admin/summary`를 호출하면, Then `at_risk_count`, `exceeded_count`, `ratio`는 모두 0이다.
- Given 조직 A 활성 예산 workflow에 조직 B로 명시된 고비용 usage가 있다, When 조직 A의 `GET /admin/summary`를 호출하면, Then 해당 usage는 at_risk/exceeded 판정에 반영되지 않는다. NULL organization legacy usage는 계속 반영한다.
- Given 조직 A 예산 workflow에 조직 A usage, NULL legacy usage, 조직 B로 명시된 usage가 함께 있다, When 조직 A의 `GET /admin/workflow-budgets/{workflow_id}`를 호출하면, Then `current_month_cost`, `usage_ratio`, `status`는 조직 A와 NULL usage만 반영한다.
- Given 같은 혼합 usage가 있다, When 조직 A의 `PUT /admin/workflow-budgets/{workflow_id}`로 예산을 저장하면, Then 저장 응답의 `current_month_cost`, `usage_ratio`, `status`도 조직 A와 NULL usage만 반영한다.
- Given 단건 예산의 당월 canonical operation에 `provider_started` 또는 `outcome_unknown`이 남아 있다, When GET 또는 PUT 응답을 받으면, Then 계산 가능한 `current_month_cost`와 함께 `usage_data_complete=false`, 양수 `unresolved_provider_call_count`가 반환된다.
- Given 활성 예산 workflow가 0개인 조직, When summary를 조회하면, Then `budget`은 null이다.
- Given 활성 예산이 있는 App, When member가 `GET /apps`를 호출하면, Then 항목의 `budget_status`에 `usage_ratio`, `status`만 포함되고 예산 금액/비용 원문은 포함되지 않는다.
- Given 활성 예산이 있는 App, When organization manager 또는 workflow `write` 이상 사용자가 `GET /apps/operations`를 호출하면, Then row의 `app.budget_status`에 `usage_ratio`, `status`만 포함되고 `/dashboard/mymodule`은 이 값을 표시 원천으로 사용한다.
- Given 예산 미설정 App 또는 `workflow_id`가 null인 App, When `GET /apps` 또는 `GET /apps/operations`를 호출하면, Then `budget_status`는 null이고 기존 응답 필드는 변하지 않는다.
- Given App의 primary workflow에는 활성 예산이 없고 같은 `app_id`의 과거/보조 workflow에는 활성 예산과 초과 비용이 있다, When `GET /apps` 또는 `GET /apps/operations`를 호출하면, Then 해당 App의 `budget_status`는 null이고 보조 workflow의 `exceeded` 상태를 대신 표시하지 않는다.
- Given `workflow_id=null`인 App과 같은 `app_id`를 가진 과거/보조 workflow에 활성 예산이 있다, When `GET /apps` 또는 `GET /apps/operations`를 호출하면, Then 해당 App의 `budget_status`는 null이다.
- Given `GET /apps` 또는 `GET /apps/operations` 응답 대상에 여러 App의 primary workflow가 포함된다, When `budget_status`를 계산하면, Then primary workflow별 당월 비용은 grouped query로 계산하고 App row마다 개별 집계를 반복하지 않는다.
- Given App의 primary workflow에 활성 예산이 있고 같은 workflow의 당월 usage row 중 `llm_usage_logs.organization_id`가 NULL인 기존/마이그레이션 로그가 있다, When `GET /apps` 또는 `GET /apps/operations`의 `budget_status`를 계산하면, Then NULL organization usage도 합산해 실행 차단 판정과 같은 `status`를 반환한다.
- Given 예산 100 USD와 당월 비용 79.99/80.00/100.00/100.000001 USD인 App들이 있다, When `GET /apps` 또는 `GET /apps/operations`를 호출하면, Then `budget_status.status`는 각각 `normal`/`at_risk`/`at_risk`/`exceeded`다.
- Given KST 월 경계 row가 App의 primary workflow에 기록되어 있다, When `budget_status`를 계산하면, Then KST 당월 `[start, end)` 경계 기준으로 포함/제외한다.

### AC-5. 실행 차단 (BGT-REQ-030~034)

- Given `exceeded` 상태 workflow, When `POST /workflows/{id}/execute` 또는 `/stream`을 호출하면, Then `429`와 오류 코드 `budget.exceeded`가 반환되고 Celery task는 dispatch되지 않는다.
- Given `exceeded` 상태 workflow의 활성 배포, When `POST /run/{slug}`(api)와 `POST /run-public/{slug}`(app)를 호출하면, Then 동일한 `429 budget.exceeded`로 차단된다.
- Given `exceeded` 상태 workflow의 webhook/schedule trigger, When trigger가 발화하면, Then 실행이 dispatch되지 않고 차단 audit이 기록된다.
- Given 차단 응답, When body를 확인하면, Then 예산 금액과 당월 비용 원문이 포함되지 않는다.
- Given `at_risk` 상태(80% 이상 100% 이하) workflow, When 실행하면, Then 차단되지 않고 정상 실행된다.
- Given `exceeded` 상태 workflow, When `POST /workflows/{id}/compare`(A/B 비교)를 호출하면, Then 차단되지 않는다 (BGT-REQ-032).
- Given 예산 미설정 workflow, When 모든 실행 경로(테스트/api/app/webhook/schedule)를 실행하면, Then 예산 로직 없이 기존과 동일하게 동작한다 (NFR-005 기존 경로 보존).
- Given 활성 예산 workflow에서 당월 비용 집계 실패(DB 오류 주입), When 실행을 요청하면, Then fail-closed로 차단된다 (BGT-REQ-033).
- Given 월이 바뀐 직후(전월 exceeded, 당월 비용 0), When 실행을 요청하면, Then 차단되지 않는다.

### AC-6. 동시성 (BGT-REQ-005, BGT-REQ-034~035)

- Given 예산 row가 없는 workflow, When 두 owner/manager가 서로 다른 금액으로 동시에 PUT하면, Then row는 정확히 1개 생성되고, 두 요청 모두 5xx 없이 성공하며, 최종 값은 나중에 커밋된 쪽이다. audit은 `workflow_budget.created` 1회 + `workflow_budget.updated` 최대 1회만 기록된다.
- Given 기존 예산 row, When 두 요청이 동시에 서로 다른 값으로 PUT하면, Then 최종 상태는 어느 한쪽 값과 정확히 일치하고 (두 값이 섞이지 않음) 각 갱신마다 `workflow_budget.updated`가 기록된다.
- Given App primary Workflow에 활성 예산이 있다, When Agent Builder `new_workflow` apply/save로 primary 교체를 요청하면, Then `APP_WORKFLOW_BUDGET_CONFLICT`로 차단되고 예산·usage·App primary는 변경되지 않으며 응답에 금액 원문이 없다.
- Given primary 전환의 활성 예산 확인과 같은 Workflow budget upsert가 경합한다, When 두 transaction이 실행되면, Then 같은 advisory scope lock으로 직렬화되고 5xx나 부분 복제가 발생하지 않는다.
- Given budget PUT이 App의 old primary를 관찰한 뒤 primary 전환에 밀리거나 이미 non-primary인 Workflow를 대상으로 한다, When App lifecycle lock을 획득해 canonical primary를 재검증하면, Then `409 workflow.primary_changed`로 실패하고 old Workflow 예산 row와 audit을 만들지 않는다.
- Given 예산 수정/비활성화와 `GET /apps` 또는 `GET /apps/operations` 조회가 동시에 발생하면, Then 조회 응답은 5xx 없이 완료되고 `budget_status`는 수정 전 값, 수정 후 값, 또는 비활성화 후 null 중 하나로 일관되게 반환된다.
- Given workflow 삭제로 예산 row가 cascade 삭제되는 중 `GET /apps/operations`가 실행되면, Then 권한/목록 조회에서 이미 제외된 row는 반환하지 않고, 응답 대상 App에서 예산을 찾을 수 없으면 `budget_status=null`로 처리한다.
- Given workflow `execute` 전용 사용자가 `GET /apps/operations`를 호출하면, Then 해당 workflow row와 예산 상태는 응답에 포함되지 않는다.
- Given 초과가 `llm_usage_logs`에 이미 반영된 workflow, When 여러 실행 요청이 동시에 도착하면, Then 전부 `429 budget.exceeded`로 차단된다 (보장 하한선, BGT-REQ-034).
- Given 사용률 99%인 workflow, When 실행 요청 N개가 동시에 판정을 통과해 dispatch되면, Then 이는 수용된 한시적 초과(BGT-REQ-035)이며, 이 실행들의 비용이 기록된 이후의 신규 요청은 모두 차단된다. (테스트는 "통과 자체"가 아니라 "기록 반영 후 차단 전환"을 검증한다.)
- Given 실행 판정과 동시에 예산이 비활성화되는 경합, When 두 동작이 겹치면, Then 실행은 수정 전/후 어느 한쪽 기준으로 일관되게 판정되고 5xx가 발생하지 않는다.
- Given KST 월말 23:59와 월초 00:00에 걸친 실행 요청, When 각각 판정하면, Then 각 요청은 자신의 판정 시점 KST가 속한 달의 집계를 사용한다.

### AC-7. Audit (BGT-REQ-040~042)

- Given 예산 생성/수정/비활성화, When audit log를 조회하면, Then `workflow_budget.created`/`workflow_budget.updated`가 target_type `workflow_budget` 기준으로 기록되고 metadata는 `monthly_budget_usd`, `is_enabled` 운영 summary로 제한된다.
- Given 예산 초과 차단, When audit log를 조회하면, Then `policy.block`(target_type `workflow`, status `failure`, `audit_metadata.reason='budget.exceeded'`, trigger mode 포함)이 기록된다.
- Given 활성 예산의 비용이 미확정이라 direct 실행이 `unavailable`로 차단됨, When audit log를 조회하면, Then 공개 차단 계약과 동일한 `policy.block`, `budget.exceeded`, trigger mode가 기록되고 차단 응답과 별도로 commit된다.
- Given Schedule occurrence 이후 비용이 초과되어 Gateway dispatch 또는 Worker admission에서 처음 차단됨, When audit log를 조회하면, Then 두 경로 모두 `policy.block`, workflow target, `budget.exceeded`, `scheduler` trigger를 기록하고 claim cancellation과 같은 transaction으로 commit된다.
- Given budget unavailable이 재시도 한도에 도달함, When claim을 확인하면, Then `dead_lettered`와 failure audit이 기록되고 성공한 `schedule_dispatch.deferred`로 표시되지 않는다.
- Given anonymous public 실행 차단, When audit을 확인하면, Then actor_id 없이 기록된다.
- Given 예산 관련 응답/audit/trace, Then credential 원문, raw payload, secret 값이 포함되지 않는다 (NFR-004).

## Unit Tests

Gateway service/helper 대상 (기존 pytest 패턴). 함수명은 구현 시 확정하되 아래 케이스를 커버해야 한다.

### 판정 함수 `classify_budget_usage(current_cost, budget)`

- 경계값: 79.99→`normal`, 80.00→`at_risk`, 100.00→`at_risk`, 100.000001→`exceeded` (예산 100 기준).
- 정밀도: 비교는 `Decimal`로 수행하고, float 이진 표현 오차로 경계 판정이 뒤집히는 입력(예: 예산 0.30, 비용 0.24)에서도 정확히 판정한다.
- 비활성(`is_enabled=false`), 예산 0, 음수 예산, 예산 row 없음 → null (판정 제외).
- 비용 0, 비용이 예산의 수백 배인 극단값 → 각각 `normal`/`exceeded`, 오버플로 없음.

### 당월 집계 `get_current_month_cost(workflow_id)`

- KST 월 경계 `[start, end)`: KST 7월 1일 00:00:00 정각 row 포함, 8월 1일 00:00:00 정각 row 제외, UTC 저장값(6월 30일 15:00 UTC = 7월 1일 00:00 KST) 변환 정확성.
- `total_cost` NULL row는 0으로 합산, usage row 없음은 0 반환.
- 다른 workflow row는 합산에서 제외한다. `organization_id`는 예산 row/권한 scope의 기준이지 사용량 합산 필터가 아니므로, 같은 workflow의 NULL organization legacy row는 합산한다.
- 기준 시각(`now`)을 주입받아 월 경계를 결정한다 (월말/월초 테스트 가능하도록).

### 예산 upsert `upsert_workflow_budget(...)`

- row 없음 → 생성, `created_by` 기록, `workflow_budget.created` audit.
- row 있음 → 갱신, `updated_by` 갱신, `created_by` 불변, `workflow_budget.updated` audit.
- `is_enabled=false` 갱신 → row 유지, `workflow_budget.updated` audit에 `is_enabled=false` metadata.
- 기존 값과 동일한 no-op → 갱신/audit 없음.
- 생성 경합 IntegrityError → 갱신으로 전환(재시도 1회), 5xx 미발생 (BGT-REQ-005).
- audit_metadata에 `monthly_budget_usd`, `is_enabled` 외 값(요청자 토큰, raw body 등) 미포함.

### 예산 요청 스키마 `WorkflowBudgetUpsertRequest`

- `monthly_budget_usd` 누락, 숫자 아님, 0 이하 → validation 오류.
- `monthly_budget_usd` 소수점 3자리 이상 → validation 오류.
- `monthly_budget_usd=9999999999.99` → `NUMERIC(12,2)` 최대 저장 가능 값으로 허용.
- `monthly_budget_usd=10000000000.00` 또는 `100000000000` → `NUMERIC(12,2)` overflow를 일으키는 값이므로 validation 오류. API에서는 `422`로 반환되어야 하며 service/DB commit까지 도달하지 않아야 한다.
- Request body에 `monthly_budget_usd`, `is_enabled` 외 unknown field가 있으면 validation 오류. API에서는 `422`로 반환되어야 하며 service/DB commit까지 도달하지 않아야 한다.

### 실행 차단 helper `ensure_workflow_budget_allows_execution(...)`

- 예산 미설정 workflow → 통과, 집계 쿼리를 실행하지 않는다 (기존 경로 보존 + 불필요 부하 없음).
- 비활성/0 이하 예산 → 통과.
- `normal`/`at_risk`(정확히 100% 포함) → 통과.
- `exceeded` → 차단 예외 (HTTP 계층에서 `429 budget.exceeded`로 변환).
- 활성 예산 workflow에서 집계 쿼리 예외 → fail-closed 차단 예외 (BGT-REQ-033).
- 활성 예산 workflow의 당월 canonical provider operation에 `provider_started` 또는 `outcome_unknown`이 존재 → 비용을 0으로 간주하지 않고 `unavailable`로 fail-closed한다.
- 직접 실행 helper의 `unavailable` 차단도 초과 차단과 같은 safe `policy.block` audit을 한 건 기록하며 금액·raw payload는 포함하지 않는다.
- 성공 provider operation의 compatibility projection이 pending/완료 어느 상태이든 canonical 비용은 한 번만 합산되며, operation reference가 있는 projection은 legacy 합계에서 제외한다.
- Schedule dispatch/admission의 활성 예산 집계 statement가 실패하면 savepoint만 rollback되고 outer transaction은 계속 사용 가능해야 한다. 같은 UnitOfWork에서 `budget_evaluation_failed` backoff/dead-letter 상태를 기록하고 commit할 때 `PendingRollbackError`가 발생하지 않는다.
- 매 호출마다 집계를 새로 조회한다 — 같은 helper 인스턴스/요청 컨텍스트에서 판정 캐시 없음 (BGT-REQ-034).
- compare 경로 플래그/미호출 검증 (BGT-REQ-032).

### Conversation Memory reservation target

- 동일 ProviderExecutionCapability identity/revision + billing scope + idempotency key의 동시 reserve는 하나의 reservation만 만든다.
- Credential revoke/permission decision revision 변경 뒤 stale capability는 새 reservation을 만들지 못하고, Budget consumer가 credential principal/revision을 자체 합성해 우회하지 못한다.
- Reservation 거부 또는 adapter unavailable이면 summary provider를 호출하지 않는다.
- Provider 미호출 실패는 reservation을 release하고 usage를 기록하지 않는다.
- Provider 성공 후 commit retry는 actual usage를 한 번만 계상한다.
- Provider outcome unknown은 reservation을 즉시 재사용하지 않고 reconciliation 상태로 전환한다.
- Reservation expiry와 늦은 usage commit이 경합해도 실제 usage를 누락하거나 이중 계상하지 않는다.
- Reservation capability가 없는 composition은 `window_then_summary`를 시작하지 않는다.
- 가격 정보 누락, stale pricing lookup, invalid estimate와 unknown-zero model은 `budget.price_unavailable`로 거부하고 summary provider/usage를 만들지 않는다.
- 명시적으로 가격이 0인 approved free model과 가격 미산정 때문에 0인 model을 구분하고, 후자만 fail-closed 한다.
- Capability의 organization/workflow/deployment version/node invocation/provider/model/pricing revision/purpose/token·cost cap/expiry 중 하나가 reservation scope와 다르면 provider 호출 전에 거부한다.
- Reservation, Memory context lease, provider attempt와 usage commit이 서로 다른 capability identity/revision을 사용하면 fail-closed하고 비용을 이중 계상하지 않는다.
- Billing principal, execution subject, credential principal과 audit actor가 다른 fixture에서도 Conversation Access Grant/app owner를 임의 principal로 합성하지 않는다.
- Stale pricing revision 또는 capability expiry와 concurrent reserve가 경합하면 old reservation을 새 provider call에 재사용하지 않고 safe reconciliation/re-reservation을 요구한다.

### Query embedding provider operation target

- 같은 node invocation에서 동일 canonical embedding model을 사용하는 여러 KB는 하나의 capability/provider attempt/usage operation을 공유하고 KB ID를 비용 dimension으로 저장하지 않는다.
- `purpose`, model, provider, pricing revision, `output_token_cap=0` 또는 organization billing principal이 다르면 outbound 전에 거부한다.
- Ledger intent 또는 `provider_started` 저장 실패는 embedding provider 호출 0회이고, timeout/terminal write 실패는 기존 operation을 `outcome_unknown`으로 남긴다.
- Duplicate delivery와 reconciliation은 새 embedding 호출이나 usage row를 만들지 않고 같은 stable provider attempt로 수렴한다.
- Usage/reservation/reconciliation의 fixture와 실패 출력에는 raw query, vector, credential 또는 provider payload가 없다.

### 실행 경로별 차단 연결

- `POST /workflows/{id}/execute`, `/stream` — 429 응답 shape(`budget.exceeded`), Celery `send_task` 미호출, stream은 SSE 시작 전 차단.
- `DeploymentService.run_deployment` — trigger_mode `api`/`app` 각각 429, engine 미호출.
- webhook(`POST /hooks/{slug}`) — 차단 시 dispatch 없음 + audit 기록.
- scheduler dispatch — 차단 시 실행 생략 + audit 기록, scheduler 루프는 계속 동작 (예외 전파로 다른 schedule이 죽지 않음).
- 모든 경로에서 기존 권한/인증 검사가 예산 판정보다 먼저 수행된다 (권한 없는 요청이 429가 아니라 403/404를 받는다).

### 집계/조회 응답

- admin summary budget 블록 — at_risk/exceeded 카운트, `ratio` 계산, 분모 = 활성 예산 workflow 수, 활성 예산 0개 → null, 비활성 예산 workflow는 분모/분자 모두 제외.
- `GET /admin/usage/workflows` 목록 — App과 Workflow organization이 모두 요청 organization과 일치하는 primary workflow 전체를 반환, same-organization 또는 NULL legacy usage만 합산, usage row가 없는 workflow도 prompt/completion tokens/call_count/total_cost 0으로 포함, `total`은 usage row 보유 workflow 수가 아니라 응답 대상 primary workflow 수, 비용 내림차순과 동률 안정 정렬.
- `GET /admin/usage/workflows` budget 블록 — 기간 필터와 무관하게 당월 기준, same-organization 또는 NULL legacy usage만 합산, 명시적 타 organization usage 제외, 미설정 null, 활성 예산이 있지만 eligible 당월 usage row가 없으면 current_month_cost/usage_ratio 0과 `normal`.
- `GET /admin/workflow-budgets` 목록 — 조직 scope 필터, `updated_at` 내림차순, pagination.
- `GET/PUT /admin/workflow-budgets/{workflow_id}` 단건 — 계산 가능한 당월 합계와 함께 `usage_data_complete`, `unresolved_provider_call_count`를 반환해 미확정 비용을 숨기지 않는다.
- `GET /apps` budget_status — 목록 전체가 primary workflow id 기준 grouped query 1회로 계산 (N+1 없음), 사용량 합산은 실행 차단과 동일하게 `workflow_id`+KST 월 경계 기준(`llm_usage_logs.organization_id` 필터 없음), `usage_ratio`/`status`만 포함 (금액 필드 부재 검증), `workflow_id` null인 App은 null, 같은 `app_id`의 과거/보조 workflow 예산은 무시.
- `GET /apps/operations` app budget_status — operations page/batch의 primary workflow id 기준으로 grouped query 1회 계산, 같은 workflow의 NULL organization legacy usage를 포함, `row.app.budget_status` shape는 `GET /apps`와 동일, 권한 없는 row에는 예산 상태를 노출하지 않음, `workflows.app_id` 역참조로 후보를 확장하지 않음.
- App budget_status 경계값 — 79.99/80.00/100.00/100.000001(예산 100)에서 `normal`/`at_risk`/`at_risk`/`exceeded`, `total_cost` NULL은 0, 비활성/0 이하 예산은 null.
- App budget_status 월 경계 — KST 월초 정각 포함, 다음 달 월초 정각 제외, 기준 시각은 timezone-aware KST now 사용.
- 모든 예산 응답에 secret/credential/raw payload 계열 필드 부재.

## Client Tests

Vitest 기준.

- `BudgetStatusBadge` — status별 렌더링, 사용률 % 표시 반올림.
- `BudgetEditModal` — 초기값 로드(404 → 신규 폼), 0 이하/비숫자/소수점 3자리 이상/`9999999999.99` 초과 입력 차단, 저장 성공 시 refetch 콜백, 422 필드 오류 표시, 403 권한 오류 표시.
- 내 워크플로우 목록 — 리스트 보기는 `budget_status` null이면 기존 렌더링 유지, 상태가 있으면 `BudgetStatusBadge` 표시. 그리드 카드는 예산 사용률과 상태 배지를 숨기며, 두 보기 모두 `exceeded`면 "실행 차단" 표시 + tooltip을 유지.
- 테스트 실행 429 `budget.exceeded` 응답 → 예산 초과 안내 표시 (일반 오류와 구분).

## E2E / 시나리오 연결

- PRD 시나리오 2: 관리자가 예산을 설정하고 admin summary 카드에서 위험/초과 비율을 확인한다.
- PRD 시나리오 3: 빌더가 내 워크플로우 목록의 위험 badge로 비용 위험 workflow를 발견하고, 초과 상태에서도 A/B 비교(cost-optimizer)는 실행 가능하다.
