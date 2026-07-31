# Budget Management Requirements

Status: Draft
Related Features: admin-dashboard, workflow, app-management, deployment, audit-tracing, cost-optimizer, conversation-memory, knowledge, llm-credentials

## Purpose

관리자가 workflow 단위 월간 LLM 예산을 설정/수정하고, 설정된 예산 대비 당월 사용률을 조회하며, 예산을 초과한 workflow의 실행을 서버에서 차단하는 기능을 제공한다. [PRD](../../PRD.md)의 FR-051(예산 설정/수정)과 FR-052(내 워크플로우 예산 사용률 표시)를 담당하고, [admin-dashboard](../admin-dashboard/requirements.md) FR-015(예산 위험 workflow 요약)의 예산 데이터 원천이다.

기존 Moduly 코드에는 예산 개념이 없었고, 이 feature는 이미 축적되는 `llm_usage_logs.total_cost` 위에 예산 저장 구조, 판정/조회 표면, 실행 차단 경계를 추가한다.

## User Stories

- 플랫폼 관리자로서, 비용이 커질 수 있는 workflow에 월간 예산을 설정해 지출을 통제하고 싶다.
- 플랫폼 관리자로서, 예산 대비 사용률과 위험/초과 workflow를 대시보드에서 한눈에 확인하고 싶다.
- 플랫폼 관리자로서, 예산을 초과한 workflow가 더 이상 LLM 비용을 발생시키지 않도록 실행이 서버에서 차단되기를 원한다.
- 빌더로서, 내 워크플로우 목록에서 각 workflow의 예산 사용률과 위험/초과 상태를 확인해 비용 위험 workflow를 발견하고 싶다 (PRD 시나리오 3 진입점).

## Functional Requirements

### 예산 설정 (FR-051)

- BGT-REQ-001: organization owner/manager는 workflow 단위 월간 USD 예산을 설정/수정/비활성화할 수 있어야 한다. workflow당 예산은 최대 1개다.
- BGT-REQ-002: 예산 설정값은 `monthly_budget_usd`(양수, USD)와 `is_enabled`로 구성한다. `monthly_budget_usd`는 소수점 2자리까지 허용하고 `NUMERIC(12,2)` 저장 범위(`9999999999.99` 이하)를 초과하면 거부한다. 요청 body의 unknown field는 오타를 숨기지 않도록 validation 오류로 거부한다. 비활성화는 row 삭제가 아니라 `is_enabled=false` 갱신으로 표현하고, 설정값은 이력 없이 최신 상태만 유지한다.
- BGT-REQ-003: 예산 관리 API(조회/설정/수정/비활성화)는 organization owner/manager 전용이다. 그 외 조직 member의 접근은 `403 permission.denied`로 차단한다 (NFR-001, [ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)).
- BGT-REQ-005: 같은 workflow에 대한 동시 예산 설정 요청은 DB unique 제약(`UNIQUE(workflow_id)`)과 upsert(ON CONFLICT 갱신 또는 IntegrityError 재시도)로 방어한다. 생성 경합에서 row는 정확히 1개만 만들어지고, 어느 요청도 5xx로 실패하지 않으며, 마지막 쓰기가 최종 상태가 된다. audit은 실제 발생한 사건대로 `workflow_budget.created` 1회와 이후 갱신 건수만큼 `workflow_budget.updated`를 기록한다.
- BGT-REQ-006: 예산 설정 mutation은 대상 Workflow가 속한 App lifecycle row를 먼저 잠그고 canonical `apps.workflow_id`가 요청 Workflow와 일치하는지 재검증해야 한다. Primary 전환에 밀렸거나 이미 non-primary인 Workflow에 대한 PUT은 `409 workflow.primary_changed`로 거부하고 예산 row와 audit을 만들지 않는다. App lock 다음에 Workflow budget advisory lock을 획득해 primary 전환과 동일한 잠금 순서를 유지한다.
- BGT-REQ-044: Agent Builder가 기존 App의 primary Workflow를 새 Workflow로 교체할 때 현재 primary에 활성 예산이 있으면 전환을 `APP_WORKFLOW_BUDGET_CONFLICT`로 차단한다. 예산 row만 복제하거나 당월 usage를 초기화해서는 안 되며, 활성 예산 확인과 budget upsert는 같은 `(organization_id, workflow_id)` advisory transaction scope를 사용해야 한다. 차단 응답과 audit-safe metadata에는 예산 금액과 당월 비용 원문을 포함하지 않는다.
- BGT-REQ-004: 요청 organization scope([ADR-0009](../../decisions/ADR-0009-active-organization-header-context.md)) 밖 workflow의 예산 접근은 `404 resource.not_found`로 숨긴다 (ADR-0010).

### 사용률 판정

- BGT-REQ-010: 활성 예산은 `is_enabled=true`이고 `monthly_budget_usd > 0`인 예산이다. 예산 미설정, 비활성, 0 이하 예산 workflow는 위험/초과 판정과 실행 차단 대상에서 제외한다.
- BGT-REQ-011: 사용률은 `당월 비용 합계 / monthly_budget_usd`로 계산한다. Durable provider usage의 당월 비용은 `succeeded` canonical operation을 `provider_started_at`의 KST(Asia/Seoul) 달력 월 경계로 합산하고, 같은 operation을 가리키는 compatibility `llm_usage_logs` row는 중복 합산하지 않는다. 기존 데이터는 provider usage operation reference가 없는 `llm_usage_logs.total_cost`를 같은 월 경계로 합산하며, usage row가 없거나 `total_cost`가 NULL인 row는 0으로 합산한다. 실행 차단 판정의 비용 합산 범위는 `workflow_id`와 월 경계로 결정한다. 예산 row 조회와 접근 권한은 organization scope로 제한하지만, legacy `llm_usage_logs.organization_id`는 nullable/denormalized 호환 컬럼이므로 NULL row를 제외하는 필수 조건으로 사용하지 않는다. 따라서 `workflow_id`가 일치하는 기존/마이그레이션 로그는 `organization_id`가 NULL이어도 합산한다. 관리자 projection은 BGT-REQ-020의 더 엄격한 tenant visibility 조건을 적용하며, 이 조건이 실행 차단 판정의 합산 범위를 완화하지 않는다.
- BGT-REQ-012: 상태는 반올림 전 사용률 값으로 판정한다: 80% 미만은 `normal`, 80% 이상 100% 이하는 `at_risk`, 100% 초과는 `exceeded`.

### 사용률 조회 (FR-052, FR-015)

- BGT-REQ-020: 관리자 workflow 사용량 조회(`GET /admin/usage/workflows`)는 App과 Workflow의 organization이 모두 요청 organization과 일치하는 App primary workflow(`apps.workflow_id`) 전체를 반환하고, 각 응답 항목에 예산 블록(예산 금액, 당월 비용, 사용률, 상태)을 포함한다. 조회 기간 usage와 예산 블록의 당월 비용은 요청 organization과 같은 usage 또는 NULL legacy usage만 포함하며, 다른 organization UUID가 명시된 usage는 제외한다. 기간 안에 eligible usage row가 없는 workflow도 응답에 포함하며 사용량 필드와 비용은 0이다. 예산 블록은 조회 기간 필터와 무관하게 항상 당월(KST) 기준으로 계산한다. 활성 예산이 없는 workflow는 null이다.
- BGT-REQ-021: admin summary(`GET /admin/summary`)의 `budget` 블록을 실제 예산 데이터 기준으로 반환한다. 각 workflow의 당월 비용은 요청 organization과 같은 usage 또는 NULL legacy usage만 포함하고, 다른 organization UUID가 명시된 usage는 관리자 summary 판정에서 제외한다. `at_risk_count`, `exceeded_count`, 호환용 `ratio`를 포함하고, `ratio`의 분모는 조직의 활성 예산 workflow 수다 (admin-dashboard Open Question 확정). 활성 예산 workflow가 0개면 `budget`은 null이다 ("예산 미설정" 표시).
- BGT-REQ-022: 내 워크플로우 목록/운영 현황 원천인 `GET /apps/operations`의 App summary에 additive 필드 `budget_status`(사용률, 상태)를 추가한다. App의 primary workflow(`apps.workflow_id`) 기준이며, 활성 예산이 없거나 `workflow_id`가 null이면 null이다. 같은 `app_id`에 과거/보조 Workflow row가 남아 있어도 `apps.workflow_id`가 아닌 workflow의 예산 상태를 대신 표시하지 않는다. 예산 금액과 당월 비용 원문은 관리자 표면에만 노출하고 운영/목록 요약(`budget_status`)에는 사용률과 상태만 노출한다. `GET /apps/operations` 자체는 organization manager 또는 workflow `write` 이상 권한을 가진 운영 사용자에게만 row를 반환한다.
- BGT-REQ-023: 기존 App 목록 소비자가 동일한 안전 예산 상태 요약을 재사용할 수 있도록 `GET /apps`의 `AppResponse`에도 `budget_status`를 같은 shape로 추가한다. `GET /apps`와 `GET /apps/operations` 모두 응답 App의 primary workflow id만 모아 workflow별 당월 비용을 grouped query로 계산해야 하며, App row마다 개별 비용 집계를 수행하는 N+1 구현은 허용하지 않는다. 이 grouped query의 사용량 합산 기준은 BGT-REQ-011과 같아야 하며, `llm_usage_logs.organization_id == organization_id` 조건으로 legacy NULL 로그를 제외하면 안 된다. `workflows.app_id` 역참조를 사용한 누락 복구나 후보 확장은 목록 응답 계산 경로에서 수행하지 않는다.
- BGT-REQ-024: 관리자 예산 단건 조회와 설정 응답(`GET/PUT /admin/workflow-budgets/{workflow_id}`)의 `current_month_cost`, `usage_ratio`, `status`는 예산 row의 organization과 같은 usage 또는 NULL legacy usage만 포함한다. 응답은 `usage_data_complete`와 `unresolved_provider_call_count`를 함께 반환해 `provider_started`/`outcome_unknown`이 남은 계산 가능 합계를 확정 비용처럼 표시하지 않는다. 다른 organization UUID가 명시된 usage는 관리자 응답에서 제외한다. 이 관리자 projection 조건은 BGT-REQ-011의 실행 차단 합산 범위를 변경하지 않는다.

### 실행 차단

- BGT-REQ-030: `exceeded` 상태 workflow의 실행은 Celery dispatch 전에 Gateway service 경계에서 차단한다. 대상 경로는 테스트 실행(`POST /workflows/{id}/execute`, `POST /workflows/{id}/stream`)과 배포 실행 전 trigger mode(`POST /run/{slug}`(api), `POST /run-public/{slug}`(app), webhook, schedule)다.
- BGT-REQ-031: 차단 응답은 모든 경로에서 일관된 오류 코드 `budget.exceeded`(HTTP 429)를 사용한다. 응답 메시지에 예산 금액과 당월 비용 원문을 포함하지 않는다 (public 경로에서 조직 내부 값 노출 방지).
- BGT-REQ-032: LLM 노드 A/B 비교(`POST /workflows/{id}/compare`, cost-optimizer)는 차단 대상에서 제외한다. 초과 workflow의 비용 최적화 작업(PRD 시나리오 3)이 복구 경로이기 때문이다. compare 실행으로 발생한 LLM 비용은 사용률 집계에 포함된다.
- BGT-REQ-033: 차단 판정은 fail-closed다. 활성 예산 workflow에서 당월 비용 집계에 실패하거나 `provider_started`/`outcome_unknown` canonical operation이 남아 비용 완결성을 확인할 수 없으면 `unavailable`로 실행을 차단한다. 미해결 provider operation을 0원으로 확정하지 않는다. 활성 예산이 없는 workflow는 판정 로직을 건너뛰어 기존 실행 경로가 깨지지 않아야 한다 (NFR-005).
- BGT-REQ-034: 판정은 매 실행 요청마다 dispatch 직전에 canonical ledger와 operation reference가 없는 legacy usage를 DB에서 집계한다. 판정 결과를 캐시하거나 이전 요청의 판정을 재사용하지 않는다. 성공 operation은 compatibility projection 지연과 무관하게 즉시 반영하며, 초과 비용이 durable하게 확정된 이후 도착하는 모든 신규 실행 요청은 경로와 무관하게 차단되어야 한다 (동시성 방어의 보장 하한선).
- BGT-REQ-035: 일반 workflow 실행의 동시 실행으로 인한 한시적 초과(overshoot)는 알려진 한계로 수용한다. 비용은 LLM 호출 완료 후 기록되므로 in-flight 실행의 비용은 dispatch 시점 판정에 반영될 수 없고, dispatch 직렬화(분산 락)로도 이 창은 닫히지 않는다. 초과 폭은 "차단 확정 전에 dispatch된 동시 실행들의 비용"으로 한정되며, 이미 시작된 실행은 중단하지 않는다. 일반 실행 전체에 대한 예약 모델은 1차 구현 범위 밖이다.
- BGT-REQ-036: Schedule dispatch/admission transaction 안의 예산 조회·집계는 nested transaction/savepoint로 격리해야 한다. DB statement 실패를 `unavailable`로 변환할 때 실패한 savepoint를 먼저 rollback하여 caller가 보유한 canonical row lock과 outer UnitOfWork를 유지하고 bounded retry/dead-letter 상태를 같은 transaction에서 기록할 수 있어야 한다.
- BGT-REQ-037 (Conversation Memory Target Extension): Memory summary처럼 동일 logical operation의 provider 호출을 단일화해야 하는 consumer를 위해 예상 비용 reservation, actual usage commit, release와 unknown outcome reconciliation capability를 제공해야 한다. 이 capability가 구현되기 전 Conversation Memory는 summary provider 호출을 활성화하지 않고 window-only 또는 fail-closed로 동작해야 한다. 가격이 없거나 invalid/unknown 때문에 estimate가 0인 model은 `budget.price_unavailable`로 reservation을 거부하고 provider를 호출하지 않아야 한다.
- BGT-REQ-038 (Conversation Memory Target Extension): Reservation은 LLM Credentials domain이 발급한 opaque `ProviderExecutionCapability` identity/revision, server-derived billing principal, Budget에 필요한 purpose/pricing/cap/expiry binding과 idempotency scope를 canonical하게 받아야 한다. Budget은 credential principal 또는 capability permission revision을 자체 계산하지 않으며 같은 idempotency key/capability의 중복 예약·commit을 만들지 않아야 한다.
- BGT-REQ-039 (Conversation Memory Target Extension): Reservation, Memory context lease, provider attempt와 usage reconciliation은 같은 ProviderExecutionCapability identity/revision과 각 operation binding을 검증해야 한다. Stale/expired capability, wrong invocation/admission/attempt/purpose/pricing binding 또는 cap 초과는 provider 호출 전에 fail-closed해야 한다.
- BGT-REQ-039A (Conversation Memory Target Extension): Billing principal은 execution subject, credential principal과 audit actor와 분리해야 한다. Conversation Access Grant나 app/deployment owner를 임의로 billing/execution/audit principal로 전환하지 않아야 한다. Reservation 만료와 늦은 usage commit 경합은 실제 비용을 누락하거나 동일 비용을 이중 계상하지 않도록 reconciliation해야 한다.
- BGT-REQ-039B (Query Embedding Target Extension): RAG query embedding은 distinct canonical embedding model마다 ADR-0071의 capability identity/revision, organization billing principal, pricing snapshot, `output_token_cap=0`과 stable provider attempt에 binding된 ADR-0069 durable provider operation을 사용해야 한다. Intent와 `provider_started`가 provider 전송 전에 확정되지 않거나 usage ledger가 주입되지 않으면 provider를 호출하지 않는다. Raw query와 vector는 reservation, usage 또는 reconciliation data에 저장하지 않는다.

### Audit

- BGT-REQ-040: 예산 생성/수정/비활성화는 canonical action `workflow_budget.created`/`workflow_budget.updated`로 audit에 기록한다. `audit_metadata`에는 `monthly_budget_usd`, `is_enabled` 같은 운영 summary만 포함한다. 비활성화는 `workflow_budget.updated`에 `is_enabled=false` metadata로 표현한다. [ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md) canonical action table 갱신이 필요하다.
- BGT-REQ-041: 예산 초과 또는 BGT-REQ-033의 비용 미확정 `unavailable`로 직접 실행이 차단되면 `policy.block`(target_type `workflow`, status `failure`, public 오류와 동일한 `audit_metadata.reason='budget.exceeded'`, trigger mode 포함)으로 기록한다. 별도 결과 중심 action(`workflow.budget_blocked` 등)은 만들지 않는다 (ADR-0008 원인 중심 명명).
- BGT-REQ-043: Schedule은 occurrence 생성, Gateway publish 직전, Worker admission 직전의 세 예산 판정에서 동일한 BGT-REQ-041 audit 계약을 사용한다. 최종 Worker 판정에서 차단되거나 budget 집계가 unavailable인 경우에도 claim 상태와 audit을 같은 transaction에 기록한다.
- BGT-REQ-042: credential 원문, raw payload, secret 값은 예산 관련 응답/audit/trace에 노출하지 않는다 (NFR-004). 예산 금액과 비용 집계값은 secret이 아니며 관리자 표면과 audit metadata에 포함할 수 있다.

## Policies And Edge Cases

- 예산 판정과 "당월" 경계는 KST 달력 월 고정이다. 저장은 UTC(timestamptz) 그대로 두고 집계 경계만 KST로 계산한다 (admin-dashboard 시간대 규칙과 동일).
- 차단 기준은 상태 판정과 동일하게 사용률 100% 초과다. 사용률이 정확히 100%인 실행 요청은 차단하지 않는다 (`at_risk`).
- 월이 바뀌면 당월 비용이 0에서 다시 시작하므로 전월 초과 workflow는 자동으로 차단이 풀린다. 별도 초기화 작업은 없다.
- anonymous public 실행(`/run-public`)의 차단 audit은 actor 없이(actor_id null) 기록한다.
- workflow 삭제 시 예산 row는 함께 삭제한다 (FK cascade). App의 `workflow_id`가 null이면 `budget_status`는 null이다.
- App에 과거/보조 Workflow row가 남아 있더라도 목록/운영 표면의 `budget_status`는 `apps.workflow_id`가 가리키는 workflow의 상태만 반영한다. 관계 누락이나 고아 Workflow 정리는 별도 정합성 복구 경로에서 다루며, `GET /apps`/`GET /apps/operations` 응답 생성 중 보조 workflow 예산 상태를 대체값으로 사용하지 않는다.
- 관리자 비용 탭의 `GET /admin/usage/workflows`도 App primary workflow 전체를 목록 기준으로 삼는다. 기간 안에 usage가 없는 workflow는 비용 0 row로 표시되어야 하며, 예산 설정 진입에서 누락되면 안 된다.
- `GET /apps`/`GET /apps/operations`의 `budget_status` 조회와 예산 수정·비활성화·삭제가 경합하면 응답은 5xx 없이 완료되어야 한다. 각 row의 `usage_ratio`와 `status`는 같은 조회 스냅샷 기준으로 일관되면 되며, 경합 결과 활성 예산을 찾을 수 없으면 `budget_status=null`로 처리한다.
- 예산 수정/비활성화가 진행 중인 실행에 소급 적용되지 않는다. 판정은 dispatch 시점 스냅샷이며, 판정과 동시에 예산이 수정되는 경합에서는 수정 전/후 어느 한쪽 기준으로 일관되게 판정되면 된다 (5xx 금지).
- 월 경계(KST 자정) 근처의 실행 요청은 판정 시점의 KST가 속한 달을 기준으로 집계한다. 사용률 계산은 float 오차로 80%/100% 경계 판정이 뒤집히지 않도록 `Decimal`(원본 `NUMERIC` 정밀도)로 수행한다.
- 일반 실행에서 가격 미산정 모델 호출이 `total_cost=0.0`으로 기록되는 admin-dashboard의 알려진 한계는 예산 집계에도 동일하게 적용된다. 실제 비용보다 낮게 집계되어 차단이 늦어질 수 있다 (수용). 이 한계는 BGT-REQ-037의 Conversation Memory summary reservation에 적용하지 않으며, summary는 unknown price를 fail-closed 한다.
- 프론트의 실행 버튼 차단은 UX 보조이며 최종 차단은 Gateway가 수행한다 (NFR-001).

## Open Questions

- 예산 임박(at_risk) 알림/notification 발송은 이 feature 범위 밖이다. 필요해지면 후속 feature로 다룬다.
- 조직 단위 총 예산, 팀/사용자 단위 예산은 1차 구현 범위 밖이다. 도입 시 requirements 갱신과 additive migration으로 확장한다.
