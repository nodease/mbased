# ADR-0069: Provider usage durable ledger와 compatibility projection 경계

Status: Accepted

Related ADRs: ADR-0033, ADR-0055, ADR-0064, ADR-0066, ADR-0067

## 배경

기존 Workflow LLM usage는 provider 응답 뒤 `llm_usage_logs`와 선택적 `WorkflowRun` 집계를 함께 기록했다. 이 방식에서는 비동기로 생성되는 WorkflowRun이 아직 없거나 저장이 실패하면 이미 발생한 provider 비용을 잃을 수 있고, public·system 실행의 execution subject, credential principal, billing principal과 audit actor가 legacy `user_id` 하나로 혼동될 수 있다. Provider 응답 유실이나 Worker crash 뒤 결과를 알 수 없는 호출을 자동 재시도하면 중복 비용도 발생한다.

[ADR-0064](ADR-0064-provider-execution-capability-boundary.md)는 provider 호출 전 권한과 상한을 확정하지만 capability row 자체를 장기 비용·감사 원장으로 사용하지 않는다. 호출 사실, 결과 불명 상태, 보정과 지연 projection을 별도 durable lifecycle로 소유할 경계가 필요하다.

## 결정

1. `provider_usage_operations`가 capability-required provider attempt의 canonical usage 원장이다. Canonical key는 `(organization_id, provider_attempt_id, purpose)`이며 workflow/deployment/canonical `(container_path, node_id)` location/admission, capability·policy·resource revision, 네 principal, pricing과 cap은 exact replay 비교용 immutable safe snapshot으로 저장한다. Structured path는 ADR-0066의 Shared parser로 검증하고 다른 Loop의 같은 `node_id`를 같은 binding으로 축소하지 않는다.
2. 실행 순서는 final capability admission 뒤 `intent` commit, `provider_started` commit, provider I/O, terminal outcome commit이다. 각 단계는 독립된 짧은 transaction을 사용하며 provider I/O 동안 DB session이나 row lock을 유지하지 않는다. Terminal success 저장이 실패해 별도 transaction으로 `outcome_unknown`을 기록할 때도 실패한 terminal session을 먼저 rollback/close해 operation row lock을 해제한다. `provider_started`는 provider 수신 확인이 아니라 outbound를 허용한 마지막 durable fence다.
3. `provider_started` 뒤 timeout, response loss, malformed usage, terminal 저장 실패 또는 Worker crash처럼 결과를 확정할 수 없는 경우 `outcome_unknown`으로 닫고 같은 attempt의 자동 provider retry와 fallback을 금지한다. Invocation adapter가 typed outcome-unknown을 반환하면 LLM runtime은 주기 reconciler를 기다리지 않고 같은 실행에서 즉시 unknown terminal을 commit한다. Provider가 요청을 받지 않았거나 비용·효과가 없음을 명시적으로 확정할 수 있을 때만 `failed_definitive`를 사용한다. Guarded transport의 typed `before_send`는 application-level `provider_not_sent`로, 현재 provider HTTP 계약의 인증·인가 거절 `401`/`403`은 `provider_rejected`로 번역해 즉시 definitive terminal을 commit한다. `429`, `5xx`와 그 밖의 response-received 오류는 이 확정 거절로 승격하지 않는다. 조사로 결과가 확정된 unknown은 Gateway image의 no-replay 운영 명령만 사용해 exact organization, operation ID와 expected state version을 제시하고 `succeeded` 또는 safe definitive reason으로 한 번 전이한다. 이 명령은 provider client나 raw payload를 입력받지 않으며 사람 actor 추적은 platform IAM/명령 실행 감사가 소유한다.
4. Actual usage는 admission이 봉인한 input/output 가격으로 계산하고 admitted token·cost 상한을 넘거나 정수형 non-negative usage 계약을 만족하지 않으면 성공으로 기록하지 않는다. Runtime 최초 success뿐 아니라 late success reconciliation과 correction도 domain 경계에서 sealed admitted token, immutable 가격으로 다시 계산한 비용과 cost cap을 독립적으로 검증한다. Immutable 가격은 intent 생성 시 원장 컬럼과 같은 `NUMERIC(20,9)` fixed scale로 값 손실 없이 정규화한다. 9자리보다 정밀해 반올림이 필요하거나 컬럼 precision을 넘는 값은 intent commit 전에 거부하며 replay 비교는 이 canonical 표현만 사용한다. Prompt, completion, credential/config, provider request/response와 raw exception은 ledger, correction, audit, projection 운영 상태에 저장하지 않는다.
5. `llm_usage_logs`는 기존 화면과 WorkflowRun 집계를 위한 compatibility projection이다. `provider_usage_operation_id` nullable unique identity와 usage revision으로 같은 operation을 한 행에 수렴시킨다. WorkflowRun이 아직 없으면 nullable run으로 먼저 projection하고 `projection_status=awaiting_workflow_run`으로 늦은 연결만 대기하며, 나중에 exact workflow correlation이 확인될 때만 연결하고 `projected`로 닫는다. Exact intent replay가 nullable WorkflowRun 또는 cost-optimizer candidate correlation을 처음 보강하면 완료 projection 중 해당 correlation projection만 다시 연다. 완전히 닫힌 `projected` 행은 주기적 projection recovery 인덱스와 claim 대상에서 제외한다. 서로 다른 operation이 같은 WorkflowRun 합계를 갱신할 때는 해당 run 행을 `populate_existing + FOR UPDATE`로 직렬화해 각 delta를 최신 committed 합계에 더한다. Run 종료 시 legacy usage 합계를 재계산하는 writer도 동일한 fresh row lock을 먼저 획득해 projection delta를 stale 값으로 덮어쓰지 않는다. 삭제된 optional credential/model/workflow/candidate reference는 projection에서 NULL로 내리되 immutable ledger snapshot은 유지한다. 필수 legacy user가 이미 없으면 비용 원장은 보존하고 projection은 terminal failure로 종료한다.
6. Canonical 비용 조회는 operation reference가 없는 기존 `llm_usage_logs`와 `succeeded` ledger operation을 합산한다. Ledger-linked compatibility row는 다시 합산하지 않는다. ADR-0055가 소유하는 `runtime_surface=agent_builder_intent` planner/repair 행은 capability-required Workflow LLM attempt가 아니므로 이 PR에서 새 ledger로 이관하지 않고 operation reference 없는 authoritative legacy usage로 합산한다. 비용 귀속 시각은 ledger는 `provider_started_at`, legacy usage는 기존 `created_at`이다. `provider_started` 또는 `outcome_unknown`이 남은 활성 예산 판정은 비용을 0으로 보지 않고 fail-closed하며 관리자와 My Module 비용 조회는 계산 가능한 합계와 incomplete signal을 함께 반환한다. Workflow 예산과 사용자별 model 비용 조회는 각각 실제 equality/range predicate와 일치하는 workflow-leading 및 user subject-leading 부분 인덱스를 사용한다.
7. Provider 보고나 billing reconciliation은 append-only `provider_usage_corrections`의 deterministic correction key와 usage revision으로 적용한다. Correction measurement도 기존 immutable pricing/admitted-token/cost-cap 계약을 통과해야 한다. 유효한 correction은 측정값이 현재 값과 같더라도 receipt를 추가하고 usage revision을 증가시켜 correction key를 소비한다. 같은 correction key와 같은 payload의 replay는 추가 revision 없이 현재 operation으로 수렴하고, 같은 key의 다른 payload는 conflict로 거부한다. Compatibility projection은 기존 행을 최신 revision으로 갱신한다.
8. 첫 post-call durable classification은 operation ID에서 파생한 deterministic event/idempotency key로 `llm.call` Audit Outbox 한 건을 같은 transaction에 추가한다. Actor는 admitted audit actor를 사용하고, ledger가 `workflow_run_id`를 이미 보유하면 Outbox의 top-level typed correlation에도 전달하며 immutable snapshot의 `workflow_id`를 safe metadata로 함께 보낸다. Audit 저장은 Run이 같은 organization에 속하는지만 보지 않고 이 exact workflow와도 일치할 때만 typed correlation을 보존한다. Run이 아직 없으면 기존 Audit Outbox bounded retry/nullable correlation 계약을 따르며, late reconciliation과 correction은 같은 `llm.call`을 다시 발행하지 않는다.
9. Log System은 stale `provider_started`와 pending/retryable/`awaiting_workflow_run` compatibility projection을 bounded `SKIP LOCKED` batch로 조정한다. Stale started operation은 provider를 호출하지 않고 `outcome_unknown`으로 분류한다. 전역 복구 스캔은 각각 state/status를 고정한 부분 인덱스와 `(provider_started_at, id)` 순서를 사용해 organization 선두 인덱스나 완료된 `projected` history의 full scan에 의존하지 않는다. 현재 운영 기본값은 60초 주기, started 15분 경과, projection 2분 lease와 실패 후 1분 재시도이며 배치 상한은 코드의 bounded 설정을 따른다. 이 값은 안전 불변식을 약화하지 않는 범위에서 운영 조정할 수 있다.
10. Ledger는 organization 경계 외 capability, policy, deployment, workflow, credential, model, user, WorkflowRun 같은 control row에 cascade FK를 두지 않는다. Control resource revoke/delete는 새 호출을 차단하지만 이미 발생한 usage/correction/audit fact를 지우지 않는다. Organization별 retention, legal hold와 physical purge 기간이 승인되기 전에는 자동 purge를 추가하지 않는다. 같은 이유로 schema downgrade는 `provider_usage_operations`를 `ACCESS EXCLUSIVE`로 잠근 뒤 canonical row가 0개일 때만 허용한다. Compatibility projection이 존재한다는 이유로 canonical operation/correction을 자동 삭제하거나 축약해서는 안 된다.
11. Query embedding처럼 새 provider usage purpose가 추가되면 별도 원장을 만들지 않고 authoritative capability purpose, billable classifier와 같은 operation lifecycle을 확장한다. Purpose 추가는 capability owner와 비용 문서·테스트를 함께 갱신해야 한다.

## 검토한 대안

### `llm_usage_logs`를 계속 canonical 원장으로 사용

기존 UI 호환성은 단순하지만 WorkflowRun 및 live catalog FK의 생성·삭제 순서와 provider 비용 보존이 결합된다. Canonical fact와 조회 projection을 분리한다.

### Provider 오류를 일반 Celery retry로 다시 호출

요청 전달 여부를 모르는 상태에서 두 번째 호출이 발생할 수 있다. Availability보다 중복 비용 방지를 우선하고 outcome unknown을 명시적으로 격리한다.

### Capability row에 token/cost를 누적

Capability는 짧은 실행 제어 lifecycle이고 deployment 삭제와 함께 정리될 수 있다. 장기 billing/audit fact와 control lifecycle을 결합하지 않는다.

### Live resource FK를 ledger에 유지

Credential/model/user/workflow 삭제가 historical usage를 지우거나 삭제 자체를 막을 수 있다. Safe identifier/revision snapshot을 사용하고 현재 화면용 reference만 compatibility projection에서 확인한다.

### Compatibility projection만 남기고 non-empty ledger를 자동 downgrade

성공 token/cost 일부는 보존할 수 있지만 immutable principal·revision·outcome, 미확정 operation과 append-only correction provenance를 잃는다. Retention과 legal hold가 승인되지 않은 canonical 원장을 migration이 임의로 축약할 수 없으므로 non-empty downgrade를 fail-closed한다.

## 결과

- Provider 응답과 WorkflowRun 생성 순서가 달라도 비용 사실은 먼저 보존된다.
- Public·system 실행의 subject와 actor가 credential principal로 바뀌지 않는다.
- Projection 지연은 관리자 화면 완결성에는 표시되지만 canonical 비용 누락이나 중복으로 이어지지 않는다.
- Outcome unknown은 자동 provider 재호출 없이 운영 reconciliation 대상으로 남는다.
- 같은 WorkflowRun으로 수렴하는 동시 projection은 aggregate delta를 잃지 않는다.
- Canonical row가 남아 있는 schema downgrade는 어떤 DDL도 적용하기 전에 중단된다.
- Retention과 organization erasure는 별도 승인 전까지 미결정이며, ledger 자동 purge는 제공하지 않는다.
