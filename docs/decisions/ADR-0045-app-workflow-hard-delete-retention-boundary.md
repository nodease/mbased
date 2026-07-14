# ADR-0045: App/Workflow hard delete와 운영 이력 보존 경계

Status: Accepted
Related ADRs: ADR-0004, ADR-0008, ADR-0010, ADR-0029, ADR-0032, ADR-0035, ADR-0038

## Context

현재 `AppService.delete_app()`은 `apps.workflow_id`를 비운 뒤 `workflows.app_id` 기준 bulk delete를 실행한다. 그러나 Workflow를 참조하는 permission, usage, run, Cost Optimizer, Mail, model routing 테이블의 lifecycle이 서로 다르고 일부 FK는 `NO ACTION`, 일부는 `CASCADE`다. 그래서 기본 App 생성 시 함께 만들어지는 creator `user_workflow_permissions` row만 있어도 삭제가 FK 오류로 rollback되어 API가 500을 반환한다. 반대로 현재 cascade를 그대로 넓히면 실행·비용·audit·외부 효과 증거까지 함께 사라질 수 있다. 현재 DELETE router도 active organization header를 해석하지 않고 legacy string `detail` 오류를 반환한다.

App 삭제는 사용자가 편집하고 배포하는 활성 리소스를 없애는 동작이다. 실행 이력과 외부 효과 증거의 보존·삭제 기간을 임의로 바꾸는 동작은 아니다. 두 lifecycle을 DB FK cascade 하나로 표현하지 않는다.

## Options Considered

1. 모든 Workflow 참조에 `ON DELETE CASCADE`를 적용한다.
   - 구현은 단순하지만 실행, 비용, audit, 멱등성 증거가 App 삭제와 함께 사라진다.
2. App과 Workflow를 soft delete한다.
   - 이력 FK는 유지할 수 있지만 모든 조회·권한·실행 경로에 tombstone 필터가 필요하고 graph, deployment, secret reference 같은 활성 설정이 남는다.
3. 활성 리소스는 hard delete하고 운영 이력은 lifecycle FK에서 분리한다.
   - migration과 명시적 삭제 계획이 필요하지만 사용자 리소스 삭제와 보존 정책을 독립적으로 지킬 수 있다.

## Decision

### 삭제 대상과 보존 대상

App과 그 App에 연결된 Workflow는 hard delete한다. 같은 transaction에서 다음 활성 리소스도 삭제한다.

- team/user Workflow permission
- Workflow budget
- deployment와 schedule
- active LLM node model routing policy/configuration
- App에 귀속된 LLM node version
- 그 밖에 삭제되는 App/Workflow 없이는 사용할 수 없는 편집·배포 설정

다음 operational/history record는 각 도메인의 retention 정책까지 보존한다.

- WorkflowRun, WorkflowNodeRun, trace payload/access history
- LLM usage log
- canonical AuditLog와 Security Alert evidence
- Cost Optimizer experiment/candidate/recommendation verification
- model routing update/run/observation/cohort/evidence/validation history
- Mail message processing와 Gmail draft effect ledger
- external effect attempt
- schedule dispatch claim

보존 row의 `app_id`, `workflow_id`, `deployment_id` 등 resource ID는 삭제 뒤에도 바뀌지 않는 provenance다. 이 ID에는 App/Workflow 삭제를 전파하는 lifecycle FK를 두지 않고, App 삭제 시 `NULL`로 바꾸거나 cascade delete하지 않는다. 해당 row의 실제 purge는 각 도메인의 retention 정책만 수행한다. 명시적인 purge 정책이 아직 없는 usage, Mail, routing history도 App 삭제가 대신 지우지 않는다.

Agent Builder session/draft는 자체 만료 정책을 유지하고 App/Workflow reference만 `SET NULL`로 분리한다. 다른 Workflow graph 안의 WorkflowNode target reference는 자동으로 찾아 고치지 않는다. 저장된 graph는 그대로 두되 deployment preflight는 기존 safe reason `workflow_node_target_unavailable`, runtime은 기존 typed error `workflow_node.target_unavailable`로 provider effect 전에 삭제된 target을 차단한다.

### 삭제 집합과 동시성

삭제 use case는 active organization 안에서 App row를 먼저 잠그고, 다음으로 연결된 Workflow row를 UUID 오름차순으로 잠근다. 연결 집합은 `apps.workflow_id`와 `workflows.app_id`의 합집합이다. 잠금 뒤 organization scope와 manage 권한을 다시 확인한다.

새 run/schedule/Mail processing admission은 durable active 상태를 commit하기 전에 같은 App→Workflow 순서로 `FOR KEY SHARE` 또는 동등한 shared lifecycle lock을 잡고 resource 존재·scope를 다시 확인한다. 삭제의 exclusive lock과 이 shared lock이 경합하므로, 삭제가 먼저면 새 admission은 deleted target으로 fail-closed하고 admission이 먼저면 삭제가 active blocker를 본다. FK를 provenance ID로 바꾼 뒤에도 단순 check-then-delete race를 허용하지 않는다. 이미 running인 execution의 effect 전이는 WorkflowRun과 effect/Mail blocker가 보호한다.

정상 App은 primary Workflow 하나를 가진다. legacy 데이터는 최대 100개의 same-organization 연결 Workflow까지 같은 transaction에서 정리한다. 100개를 넘거나 primary/child organization이 다르거나 순환 연결이 손상된 경우에는 일부만 삭제하지 않고 `409 app.delete_requires_repair`로 rollback한다. 이 100개 상한은 환경별 설정이 아니라 code-owned 안전 상수다.

다음 상태가 하나라도 있으면 mutation 전에 `409 app.delete_in_progress`로 전체 삭제를 막는다.

- `running` WorkflowRun
- `pending`, `dispatching`, `enqueued`, `running` schedule dispatch claim 또는 `execution_outcome_unknown` dead letter의 review가 끝나지 않은 claim
- `prepared`/`in_flight` external effect attempt 또는 `effect_outcome_unknown + replay_same_key`로 재실행 판단이 남은 attempt
- `pending`, `processing`, `ack_pending`, `outcome_unknown` Mail processing 또는 `pending`, `claimed`, retry 대기 `failed_before_effect`, `outcome_unknown` draft effect

검사와 삭제는 하나의 transaction이다. DB 오류, audit 기록 실패, 예상하지 못한 참조 발견 시 App, Workflow, permission, config 중 어느 것도 부분 삭제하지 않는다. Schema migration은 새 service보다 먼저 적용한다. Migration과 service가 섞인 배포 구간에는 App DELETE를 일시 차단하거나 upgraded Gateway로만 라우팅해 기존 bulk delete가 새 FK 상태에서 실행되지 않게 한다.

### API와 권한

`DELETE /api/v1/apps/{app_id}` 성공 응답은 기존 호환성을 위해 `200 {"message":"App deleted successfully"}`를 유지한다.

- 미인증: `401 auth.required`
- active organization 안에 있지만 manage 권한 없음: `403 permission.denied`
- 존재하지 않음, 다른 organization, 이미 삭제됨: `404 resource.not_found`
- 진행 중이거나 결과 불명 operation 존재: `409 app.delete_in_progress`
- bounded legacy 정리 범위를 넘거나 연결 무결성 손상: `409 app.delete_requires_repair`

manage는 organization manager 또는 App primary Workflow의 `manager` 권한이다. Resource hiding은 ADR-0010을 따른다. DB 제약 이름, SQL, 내부 row 존재 여부는 오류 응답에 노출하지 않는다.

### Audit

성공한 삭제 transaction에는 canonical `app.delete` audit와 삭제된 permission의 기존 `*_permission.deleted` audit를 함께 기록한다. Audit row 기록이 실패하면 삭제도 rollback한다. 성공 event publication은 commit 뒤에만 수행하고 rollback된 요청에는 성공 audit를 발행하지 않는다.

`app.delete` metadata는 검증된 organization/actor/App/Workflow ID와 대상별 개수만 포함한다. graph, credential, secret, raw payload, trace payload, provider response는 포함하지 않는다. ORM listener를 우회하는 bulk delete로 permission audit를 생략하지 않는다.

현재 endpoint의 `@audit(AuditAction.APP_DELETE)` decorator는 새 UnitOfWork audit와 중복 기록하지 않게 제거하거나 transaction-bound recorder를 호출하는 호환 경계로 바꾼다.

## Rationale

- 사용자가 삭제하려는 활성 App은 확실히 없어지면서 비용·실행·외부 효과 증거는 retention 정책대로 남는다.
- 명시적 삭제 계획과 단일 transaction으로 FK 순서 의존과 부분 삭제를 막는다.
- lock 순서와 bounded legacy 범위가 동시 삭제의 deadlock·무제한 작업 위험을 줄인다.
- 다른 Workflow graph를 자동 수정하지 않아 사용자 작성 내용을 몰래 바꾸지 않고, 실행 경계에서 안전하게 실패한다.

## Consequences

- 여러 FK를 lifecycle-free provenance ID로 바꾸는 호환 migration과 PostgreSQL 검증이 필요하다.
- 보존 이력 조회는 삭제된 App/Workflow를 join하지 못할 수 있으므로 저장된 ID와 당시의 safe snapshot을 사용해야 한다.
- `AppService.delete_app()`의 현재 bulk delete 구현은 이 결정을 충족하지 않는다. MBA-87 구현이 완료될 때까지 이 ADR은 target contract다.
- soft delete 복구 기능은 제공하지 않는다. 삭제 확인 UX나 별도 보존 기간 변경은 후속 요구사항이다.
