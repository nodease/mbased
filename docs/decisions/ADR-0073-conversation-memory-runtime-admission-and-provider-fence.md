# ADR-0073: Conversation Memory runtime admission과 provider 전송 fence

Status: Accepted

Related ADRs: ADR-0030, ADR-0033, ADR-0064, ADR-0066, ADR-0069

## 배경

Conversation Memory foundation은 Session, Access Grant, Turn, durable dispatch,
Context Plan/Lease와 Provider Attempt schema를 제공하지만 실제 공개 Workflow 실행은
연결하지 않았다. Runtime을 연결할 때 다음 경계를 명시하지 않으면 기존 App owner를
public 실행 주체로 합성하거나, raw 입력을 broker/WorkflowRun에 복제하거나,
Memory와 usage ledger의 두 `provider_started` marker가 서로 다른 replay 결정을 내릴 수
있다.

## 결정

1. 첫 runtime surface는 명시적 conversation envelope을 사용하는
   `POST /api/v1/run-public/{url_slug}` 하나다. 동일 deployment의 legacy Memory-OFF
   요청은 기존 계약을 유지하고, 다른 실행 surface의 versioned Memory-enabled graph는
   별도 계약이 승인될 때까지 provider I/O 전에 거부한다.
2. 첫 활성 graph는 root의 `Start -> LLM -> Answer` 세 node와 두 edge로 고정한다.
   Start에는 하나의 required text input, Answer에는 LLM의 단일 text output mapping만
   허용한다. LLM은 fixed model과 bounded generation parameter allowlist만 사용하며
   routing, fallback, tool, Knowledge/RAG, structured output, nested graph와 summary는
   fail-closed한다. Gateway preflight와 Workflow runtime은 같은 pure validator를 쓴다.
3. Gateway는 current Access Grant와 frozen deployment binding을 확인한 뒤 Turn과
   dispatch를 같은 Memory transaction에서 생성한다. Broker에는 organization,
   dispatch와 Turn의 opaque reference 및 contract version만 전달한다. Raw current input,
   grant ID/token, context, provider response와 assistant output을 task argument, Celery result,
   pub/sub event, WorkflowRun 또는 WorkflowNodeRun payload에 넣지 않는다.
4. Workflow domain은 dispatch당 하나의 durable admission과 generation-fenced execution
   lease를 소유한다. Memory는 admission/running/terminal safe projection만 보존하며
   Workflow session이나 row lock을 cross-domain call 또는 provider I/O 동안 유지하지
   않는다. Current generation만 input read, context claim, provider effect, checkpoint와
   completion을 진행할 수 있다.
5. `BuildMemoryContext`는 raw content를 읽지 않고 현재 Turn 이전의 completed
   user/assistant pair reference를 최신순으로 최대 `maxTurns`개 snapshot한다.
   `ClaimMemoryContextLease`가 current authorization과 revision을 재검증하고 raw를
   process-local로 materialize한다. 선택은 newest-first contiguous barrier다. Missing,
   invalid 또는 serialized token budget을 넘는 pair를 만나면 그 pair와 더 오래된 pair를
   사용하지 않는다. 선택한 pair는 다시 시간순으로 provider context에 넣는다. Empty
   candidate는 explicit complete-empty dependency envelope이며 unknown provenance를 empty로
   승격하지 않는다.
6. Prior Memory context는 untrusted history block으로 직렬화해 현재 user message 바로 앞에
   삽입한다. System/developer prompt와 현재 user prompt의 소유권은 Workflow node에 남고
   Memory content를 instruction이나 system message로 승격하지 않는다.
7. Provider 전송 권한의 canonical source는 ADR-0069의
   `provider_usage_operations`다. 순서는 raw context claim과 최초 full-request 검증,
   usage `intent` commit, current binding 최종 재검증, Memory context-attempt marker commit,
   usage `provider_started` commit, provider I/O다. Memory marker는 context lease lifecycle만
   보호하며 독립 send authority 또는 provider 수신 증거가 아니다. Memory marker만 있고
   usage가 exact `intent`이면 current owner가 모든 binding을 다시 검증한 뒤 같은 attempt의
   usage start까지 한 번 진행할 수 있다. Usage가 `provider_started`, `outcome_unknown` 또는
   terminal이면 어떤 retry도 send 권한을 복원하지 않는다. Usage start commit이 실패하면
   provider를 호출하지 않는다.
8. Provider success 뒤 raw assistant output은 current generation의 Memory-owned provisional
   checkpoint에 먼저 한 번 저장한다. `CompleteTurn` retry는 checkpoint를 읽어 final entry를
   idempotent하게 승인하며 provider를 다시 호출하지 않는다. Public 응답과 turn status는
   승인된 mapped display projection만 반환한다.
9. Public 실행 task와 optional observer boundary는 explicit public principal과 safe opaque
   correlation만 전달한다. App creator를 user로 합성하지 않고 raw inputs/outputs, prompt,
   context, token과 provider response를 broker/result/observer payload에 넣지 않는다. Durable
   execution journal과 운영 조회 projection은 MBA-386에서 별도 retention·조회 계약과 함께
   도입하며 canonical admission, usage, Memory completion 또는 public response의 권위가 아니다.
10. Production activation은 별도 rollout/readiness gate다. Versioned worker routing,
    retention/physical purge와 legacy cutover가 준비되기 전에는 default-off를 유지한다.

## 구현 결정 기록

### MBA-318 변경 범위 경계

- Context: MBA-318 리뷰 보강 과정에서 Memory runtime 자체 외에 Workflow 월 예산 이중
  fence와 전용 execution journal까지 같은 PR에 구현되면서 변경량과 검토 경계가 커졌다.
- Options considered: 현재 구현 전체를 MBA-318에 유지, runtime 연결만 남기고 모든 안전성
  보강을 후속 처리, Memory 도입에 직접 필요한 기능과 병합 전 필수 운영 안전성만 남기고
  독립 운영 기능을 후속 이슈로 분리하는 세 방식을 검토했다.
- Final decision:
  - 이슈 직접 범위는 versioned Memory config/mapping, Public StartTurn과 durable dispatch,
    Turn/Entry 암호화·status/transcript, Workflow admission/lease, bounded context,
    ProviderExecutionCapability와 ADR-0069 usage no-replay 순서, final assistant checkpoint 및
    additive migration이다.
  - 필수 운영 안전성은 autonomous dispatch reconciliation과 terminal cleanup,
    claim/retry/crash/redelivery fencing, exact versioned queue/capable Worker, schema/key readiness,
    runtime-disabled historical transcript decrypt, organization/session-bound signed cursor,
    completed Turn/content/provider 상한, Knowledge·legacy fail-closed 및 secret redaction이다.
  - Workflow 월 예산의 Gateway/Worker 이중 fence와 차단 audit는 MBA-385, 전용 durable
    conversation execution journal과 운영 projection은 MBA-386으로 이동한다.
- Rationale: dispatch 유실, provider replay, stale owner write, transcript scope 우회와 key/readiness
  누락은 현재 Memory runtime을 안전하게 운영하기 위한 merge 전 불변조건이다. 반면 월 예산
  정책과 전용 journal은 독립된 소유 domain·migration·운영 조회/retention 결정을 가지므로
  별도 변경으로 검토하는 편이 architecture와 rollback 경계를 명확히 한다.
- Affected files: `apps/memory/`, Gateway/Workflow conversation composition과 tests,
  `apps/shared/celery_app.py`, Docker/Helm 배포 계약, Conversation Memory 문서. 제거 전 전체
  구현은 `backup/mba-318-pre-rescope-aa233352`에 보존한다.
- Follow-up review: [MBA-385](https://linear.app/yoonki1207/issue/MBA-385)는 atomic budget
  reservation 가능성을 포함해 pre-dispatch/provider-before-send fence와 audit를 검토하고,
  [MBA-386](https://linear.app/yoonki1207/issue/MBA-386)는 content-free schema, idempotency,
  organization scope, bounded retention과 운영 조회 projection을 승인한 뒤 구현한다. 두
  후속 이슈는 MBA-318의 provider no-replay와 raw-content 비영속 계약을 약화할 수 없다.

### Provider 상한의 production source

- Context: production Worker composition은 public provider 요청에 input/output token과 비용
  상한을 반드시 적용해야 하지만, 이 ADR과 배포 문서에는 승인된 운영 숫자가 아직 없다.
- Options considered: 코드에 임시 기본값을 넣는 방식, graph/public request가 상한을
  전달하는 방식, Worker 환경에서 승인된 값을 필수로 받는 방식을 검토했다.
- Final decision: `MEMORY_RUNTIME_PROVIDER_INPUT_TOKEN_CAP`,
  `MEMORY_RUNTIME_PROVIDER_OUTPUT_TOKEN_CAP`,
  `MEMORY_RUNTIME_PROVIDER_COST_CAP_MICROUSD`를 양의 bounded 정수로 모두 요구한다. 누락
  또는 잘못된 값에는 기본값을 적용하지 않고 provider I/O 전에 fail-closed한다.
- Rationale: 임의 기본값은 승인되지 않은 비용 정책을 만들고, client/graph 값은 public
  caller가 server-owned 상한을 확장하는 경로가 된다. 필수 환경값은 runtime default-off를
  유지하면서 운영 승인과 활성화를 같은 rollout gate에 묶는다.
- Affected files: `apps/workflow_engine/composition/conversation_memory.py`,
  `apps/workflow_engine/tests/test_conversation_memory_composition.py`,
  `docs/features/conversation-memory/component_spec.md`,
  `docs/features/conversation-memory/test_cases.md`.
- Follow-up review: 운영자가 모델 routing과 함께 세 상한을 승인하고 versioned Worker
  queue/capability, Memory schema 및 content key readiness를 확인하기 전에는
  `MEMORY_PUBLIC_RUNTIME_WORKER_READY=true`로 전환하지 않는다.

### Delivery 소유권과 bounded checkpoint recovery

- Context: broker duplicate delivery가 같은 owner를 공유하면 두 Worker가 하나의 live
  lease로 인정돼 늦게 도착한 delivery가 정상 provider 호출을 terminal 처리할 수 있다.
  반대로 checkpoint 뒤 DB 오류를 일반 task 실패로 ack하면 no-replay 복구 경로가 실행되지
  않는다.
- Options considered: broker message별 stable owner와 Celery retry 금지, delivery별 owner와
  무제한 retry, delivery별 owner와 bounded retry를 검토했다.
- Final decision: 각 실제 task invocation은 새 delivery owner를 사용하고, 동일 execution의
  provider attempt/checkpoint identity만 stable하게 유지한다. Active lease의 다른 owner는
  provider/Memory mutation 전에 fence하며 task 예외는 최대 3회의 bounded recovery delivery로
  다시 실행한다. Usage terminal 또는 checkpoint가 있으면 provider send 권한을 복원하지 않고
  terminal projection 또는 checkpoint completion만 재생한다. 첫 recovery countdown은
  production execution lease보다 길게 고정해 새 owner가 active lease에 막힌 채 retry
  budget을 모두 소진하지 않게 한다.
- Rationale: delivery owner는 동시 실행 fencing identity이고 provider attempt는 no-replay
  identity이므로 수명이 다르다. Bounded retry는 transient checkpoint/terminal commit 오류를
  복구하면서 무한 poison-message loop를 막는다.
- Affected files: `apps/workflow_engine/tasks.py`,
  `apps/workflow_engine/application/conversation_memory_execution.py`,
  `apps/workflow_engine/tests/test_conversation_memory_task.py`,
  `apps/workflow_engine/tests/application/test_conversation_memory_admission.py`,
  `apps/workflow_engine/tests/application/test_conversation_memory_execution.py`.
- Follow-up review: 운영 관측은 retry 고갈과 checkpoint reconciliation 실패를 safe code로
  alert하고 raw provider 응답이나 credential detail을 포함하지 않는다.

### Dispatch 재시도 고갈의 terminal scope

- Context: dispatch만 `terminal`로 바꾸면 pending Turn과 provisional user entry,
  `session.active_turn_id`가 남아 이후 모든 turn을 막는다.
- Options considered: dispatch row만 종결, 다음 POST retry에서 lazy reconciliation,
  definitive publish failure 시 Memory scope를 즉시 종결하고 exact retry도 같은 상태를
  복구하는 방식을 검토했다.
- Final decision: 최종 publish attempt가 실패하면 generation-fenced dispatch를 먼저
  terminal로 기록하고, 별도 bounded transaction이 session/Turn/user entry/dispatch binding을
  잠가 Turn failed, entry rejected, active turn released를 함께 commit한다. 이 두 번째
  transaction이 중단돼도 exact retry는 같은 terminal projection을 idempotent하게 완성한다.
- Rationale: broker send 결과와 DB transaction은 원자화할 수 없지만, dispatch terminal
  fence를 권위로 사용하면 ACK race를 덮어쓰지 않으면서 session 점유를 복구할 수 있다.
- Affected files: `apps/memory/application/dispatch.py`,
  `apps/memory/application/public_runtime.py`,
  `apps/gateway/adapters/queue/conversation_turn_publisher.py`와 관련 domain/application/Gateway
  테스트.
- Follow-up review: 운영 dispatch reconciler가 추가되면 같은 terminal finalizer를 재사용하고
  session→Turn→entry→dispatch lock 순서와 current generation 검증을 유지한다.

### Memory-on 요청의 legacy fallback 차단

- Context: public run body에서 `conversation` envelope이 빠졌을 때 versioned Memory
  deployment도 기존 legacy 실행으로 진입할 수 있었고, Memory 계약 자체가 malformed이면
  `runtime_contract_ready=false`만으로는 Memory 사용 의도를 구분할 수 없었다.
- Options considered: envelope이 있을 때만 Memory runtime을 선택, valid contract에만
  Memory runtime을 강제, frozen graph/config의 Memory-on 의도를 validation 전에 판정하는
  방식을 검토했다.
- Final decision: active public Chatbot의 frozen graph 또는 deployment config가 Memory-on을
  명시하면 contract validation 성공 여부와 무관하게 legacy fallback을 금지한다. Envelope
  누락은 provider/legacy Workflow side effect 전에 typed `422`로 닫는다. Raw graph의
  `enabled`는 literal `false` 또는 필드 부재만 Memory-off로 인정하고 legacy parser가
  coercion할 수 있는 숫자/문자열과 다른 malformed 값은 Memory intent로 fail-closed한다.
  Valid envelope 경로의 동기 DB application은 async event loop 밖의 thread-owned session에서
  실행한다.
- Rationale: malformed configuration은 Memory-OFF가 아니며 permissive fallback은 versioned
  authorization, context와 provider fence를 우회한다. DB session을 worker thread 안에서
  생성하면 request-thread session의 cross-thread 사용도 피한다.
- Affected files: `apps/shared/domain/conversation_memory_runtime.py`,
  `apps/memory/adapters/persistence/repository.py`,
  `apps/gateway/composition/memory.py`,
  `apps/gateway/api/v1/endpoints/run.py`,
  `apps/gateway/services/deployment_service.py`와 관련 테스트.
- Follow-up review: 다른 public/authenticated run surface를 추가할 때도 raw frozen contract의
  Memory-on 판정을 공유하고 각 surface의 explicit envelope/authorization 계약 없이는
  legacy 실행으로 완화하지 않는다.

### Provider deadline, prepare failure와 post-response fence

- Context: 30초 execution lease가 최대 180초 provider timeout보다 짧아 recovery owner가
  정상 요청 중 lease를 탈취할 수 있었고, permanent `provider.prepare` 오류는 Turn을
  running에 남겼다. Provider 응답 뒤 checkpoint 전에 generation이 바뀌는 race도 있었다.
- Options considered: heartbeat 도입, provider timeout 단축, timeout보다 긴 fixed lease와
  provider 응답 직후 fence 재검증을 검토했다.
- Final decision: V1 provider timeout 상한은 180초, execution lease는 210초, 첫 recovery는
  211초로 고정한다. Typed credential/configuration prepare 실패는 provider 미전송 safe
  failure로 Memory와 Workflow admission을 terminal 처리하고, untyped/transient 오류는
  bounded retry를 위해 non-terminal로 남긴다. Provider 응답 직후 current generation을
  다시 확인한 뒤에만 checkpoint와 usage success를 기록한다.
- Rationale: provider deadline 전체를 덮는 lease와 post-response fence가 stale owner의
  checkpoint를 차단한다. 영구/일시 오류를 구분하면 poison retry와 조기 terminalization을
  동시에 피한다.
- Affected files: `apps/workflow_engine/application/conversation_memory_execution.py`,
  `apps/workflow_engine/composition/conversation_memory.py`,
  `apps/workflow_engine/tasks.py`와 관련 application/composition/task 테스트.
- Follow-up review: provider timeout 정책이 180초를 넘도록 변경되면 같은 변경에서 lease와
  recovery deadline 계약도 함께 갱신하고 heartbeat/fencing 전략을 재검토한다.

### Production execution scope와 runtime schema readiness

- Context: application Port에만 execution scope 조회 계약이 있고 production SQL adapter가
  구현하지 않으면 Worker는 모든 delivery의 첫 authorization에서 실패한다. 또한 admission
  table이 readiness 집합에서 빠지면 Gateway가 runtime을 활성화한 뒤에야 Worker의 DB 오류가
  드러난다.
- Options considered: 여러 repository 조회를 조합, application에서 row를 순차 조회, 하나의
  tenant-bound locked join과 required schema capability gate를 검토했다.
- Final decision: Session, Turn, Dispatch, Access Grant, App, Workflow와 active Deployment를
  organization/turn/dispatch identity로 한 번에 조회하고 관련 row를 current transaction에서
  잠근다. Public runtime의 Workflow admission table/핵심 column도 Memory readiness의 필수
  capability로 검사한다.
- Rationale: 단일 locked scope는 authorization과 deployment binding 사이의 혼합 snapshot을
  피하고, schema gate는 provider side effect가 가능한 runtime을 incomplete migration 위에서
  시작하지 못하게 한다.
- Affected files: `apps/memory/adapters/persistence/repository.py`,
  `apps/memory/adapters/persistence/readiness.py`,
  `apps/memory/tests/adapters/test_execution_repository.py`,
  `apps/memory/tests/adapters/test_schema.py`.
- Follow-up review: execution graph topology가 확장되면 같은 scope query의 lock 순서와
  organization/deployment binding을 유지하고 필요한 additive table을 readiness capability에
  함께 추가한다.

### Expired context claim의 same-attempt recovery

- Context: context claim은 20초지만 first task recovery는 execution lease 뒤인 211초라
  provider 시작 전 crash는 반드시 expired claim으로 돌아온다. 기존 replay는 deadline을
  갱신하지 않아 복구하지 못했고, Memory marker 뒤 usage start commit 실패는 이미
  `provider_started`인 attempt를 일반 reclaim으로 처리해 영구 conflict가 됐다.
- Options considered: 매 retry에 새 provider attempt 발급, expired attempt를 상태와 무관하게
  재승인, stable attempt에서 claimed와 provider-started recovery를 분리하는 방식을 검토했다.
- Final decision: expired `claimed`만 같은 attempt에서 lease/attempt generation, deadline과
  attempt version을 CAS로 증가시키고 current authorization을 다시 검증한다. Expired
  `provider_started`는 generation/deadline과 provider send authority를 갱신하지 않으며,
  동일 capability revision과 canonical usage reference의 idempotent marker callback에만
  다시 진입한다.
- Rationale: stable attempt reclaim은 provider 미전송 crash를 복구하면서 stale owner를
  fence한다. 이미 marker가 있는 attempt를 별도로 취급하면 exact usage intent commit을
  계속할 수 있지만 provider-started/terminal usage에서 send 권한을 재생성하지 않는다.
- Affected files: `apps/memory/application/context.py`,
  `apps/memory/adapters/persistence/repository.py`,
  `apps/memory/tests/application/test_context.py`,
  `apps/memory/tests/adapters/test_context_repository.py`.
- Follow-up review: context claim과 task recovery 시간을 바꾸면 20초/211초 실제 간격의
  crash tests를 함께 갱신하고 generation CAS 및 canonical usage no-replay를 재검토한다.

### Cross-transaction terminal과 lifecycle 변경의 reference-only 수렴

- Context: context attempt, Memory Turn/checkpoint와 Workflow admission은 서로 다른
  transaction에서 commit된다. 각 commit 사이 crash 뒤 211초 recovery 전에
  close, grant revoke 또는 active deployment 교체가 일어나면 active authorization resolver는
  의도대로 실행 권한을 거부하지만, 이미 저장된 terminal/usage 사실과 provisional row까지
  정리하지 못해 Turn과 admission이 영구 잔존할 수 있다.
- Options considered: 모든 domain write를 하나의 cross-layer transaction으로 결합, active
  authorization이 돌아올 때까지 retry, immutable identity만 읽는 별도 cleanup resolver와
  current admission fence 아래의 bounded reconciliation을 검토했다.
- Final decision: deterministic admission/execution/attempt identity로 active resolve보다 먼저
  content-free recovery state를 조회한다. Runtime이 여전히 usable한 최초 `published`
  dispatch는 정상 경로로 보내고, lifecycle이 stale인 pre-ACK/queued/running 또는 이미
  terminal인 실행만 historical cleanup 후보로 인정한다. Non-terminal admission은 새 owner가
  lease generation을 획득한 뒤에만 dispatch ACK, Turn/entry/session terminal cleanup과
  admission finish를 진행한다. Completed/failed/outcome-unknown projection은 predecessor
  version, execution/attempt, assistant entry/digest와 safe reason이 정확히 일치할 때만
  재생하며 terminal admission은 active execution 권한 없이 Memory projection을 복구한다.
  Historical running cleanup은 Memory marker가 아니라 ADR-0069 usage ledger를 권위로 사용해
  `intent`는 provider 미호출 실패, `provider_started`/`outcome_unknown`은 outcome unknown,
  terminal usage는 canonical outcome으로 분류한다. Lifecycle이 이미 stale한 provisional
  assistant checkpoint는 실제 usage 사실을 보존하되 approved content로 승격하지 않고
  reject한다.
- Rationale: active authorization 거부는 새 raw read/provider I/O를 막는 경계이고, terminal
  cleanup 권한까지 없애는 경계가 아니다. Reference-only resolver와 admission fence를
  분리하면 stale owner mutation과 provider replay 없이 각 crash window를 absorbing state로
  수렴시킨다.
- Affected files: `apps/memory/application/execution.py`,
  `apps/memory/adapters/persistence/repository.py`,
  `apps/workflow_engine/application/conversation_memory_execution.py`,
  `apps/workflow_engine/adapters/conversation_memory_runtime.py`,
  `apps/workflow_engine/application/provider_usage.py`,
  `apps/workflow_engine/adapters/provider_usage.py`,
  `apps/workflow_engine/adapters/conversation_memory_provider.py`와 관련 application/adapter
  테스트.
- Follow-up review: terminal cleanup에 새 lifecycle 또는 provider usage state를 추가하면
  active send authority와 reference-only cleanup authority를 같은 branch에서 혼합하지 않고,
  각 domain commit 직후 crash와 close/revoke/redeploy 조합을 회귀 테스트로 추가한다.

### Fingerprint key rotation과 completed Turn 상한

- Context: idempotency fingerprint의 primary HMAC key가 회전하면 기존 Turn의 exact retry를 새
  primary로 계산해 false conflict를 만들 수 있었고, completed Turn 100개를 확인하지 않아
  101번째 요청도 dispatch/provider까지 도달할 수 있었다.
- Options considered: 모든 retained key로 순차 비교, 기존 Turn을 새 key로 즉시 재서명, stored
  key version으로 exact replay하고 locked Session에서 turn count를 재검사하는 방식을 검토했다.
- Final decision: 새 logical request는 primary fingerprint key를 사용하고, existing request는
  Turn에 저장된 key version 하나로만 재계산한다. Stored version이 누락되거나 retained keyring에
  없으면 새 primary로 완화하지 않고 fail-closed한다. Completed Turn 100개 상한은 preflight와
  Session row lock을 가진 mutation transaction에서 모두 검사하며 existing exact retry를 먼저
  복구한다.
- Rationale: stored version은 기존 digest를 재현하는 유일한 권위이고 Session lock 안의 두 번째
  count가 concurrent 101번째 write를 막는다. Exact retry 우선은 상한이 기존 active Turn의
  recovery를 막지 않게 한다.
- Affected files: `apps/memory/adapters/security.py`,
  `apps/memory/application/public_runtime.py`,
  `apps/memory/adapters/persistence/repository.py`와 관련 Memory/Gateway 테스트.
- Follow-up review: retained fingerprint key 제거 전 해당 version의 live Turn replay window가
  끝났는지 운영 절차에서 확인하고, 상한 정책 변경 시 preflight/locked count를 함께 갱신한다.

### Public transcript의 bounded display projection

- Context: transcript use case가 항상 빈 tuple을 반환해 completed 대화도 보이지 않았고, raw
  entry 조회를 단순 연결하면 model projection, partial failure content 또는 다른 tenant row가
  노출될 수 있었다.
- Options considered: 모든 entry를 조회해 API에서 filter, Turn별 N+1 조회, terminal Turn과
  expected user/assistant entry를 tenant-scoped bounded join으로 읽고 application에서 AAD-bound
  display projection만 여는 방식을 검토했다.
- Final decision: repository는 organization/session/terminal status/sequence로 최대 51개를
  조회하고 application은 최대 50개와 opaque cursor를 반환한다. Completed Turn은 approved
  user/assistant display ciphertext만 immutable identity와 AAD를 검증해 decrypt한다.
  Failed/cancelled Turn은 content 없이 state, timestamp와 safe reason만 반환한다.
- Rationale: bounded query와 display-only decryption이 refresh 기능을 복구하면서 model/raw
  content, partial provider output와 cross-scope row의 API 유출을 차단한다.
- Affected files: `apps/memory/application/public_lifecycle.py`,
  `apps/memory/adapters/persistence/repository.py`,
  `apps/gateway/api/v1/endpoints/public_conversation.py`와 관련 lifecycle/API 테스트.
- Follow-up review: authenticated transcript가 public과 다른 failed-user visibility를 요구하면
  별도 capability와 projection을 승인하고 public serializer를 재사용해 scope를 넓히지 않는다.

### Versioned Conversation queue의 rollout authority

- Context: exact Conversation task가 wildcard `workflow.*`의 일반 queue로 전달되면 rolling
  deployment 중 capability가 없는 old Worker가 task를 선점할 수 있었다.
- Options considered: Worker 내부 capability guard만 사용, 전용 Celery app 분리, 공통 app의
  exact versioned route와 explicit publish queue를 함께 사용하는 방식을 검토했다.
- Final decision: task name `workflow.execute_conversation_turn`은
  `conversation-memory-v1` queue로 exact route하고 publisher도 queue를 명시한다. Docker/dev
  Worker와 Helm/Compose capable deployment만 이 queue를 consume한다. Gateway activation은
  configured worker queue가 exact 상수와 다르면 startup을 거부한다.
- Rationale: broker routing이 incompatible Worker의 delivery 자체를 막고 runtime guard는
  오배달에 대한 최종 방어로 남는다. Task name과 queue 상수를 공유하면 producer/consumer
  drift를 정적 테스트로 검출할 수 있다.
- Affected files: `apps/shared/domain/conversation_memory_task.py`,
  `apps/shared/celery_app.py`, Gateway publisher/composition, Worker task, Docker/dev/Helm/Compose
  배포 파일과 deployment contract 테스트.
- Follow-up review: 새 contract version은 기존 queue의 의미를 변경하지 않고 새 versioned
  queue/capability를 추가해 drain과 rollback이 가능한 rollout을 유지한다.

### Pre-provider 실패 분류

- Context: current input/context/mapping의 영구 오류도 RUNNING Turn과 leased admission을 남겨
  poison retry가 반복됐다.
- Options considered: 모든 pre-provider 오류 terminal 처리, 모두 retryable 처리, typed
  deterministic 오류만 terminalize하는 방식을 검토했다.
- Final decision: current input unavailable, context unavailable/conflict, invalid mapping과 typed
  provider preparation failure는 current fence 아래 context attempt(있을 때), Memory Turn과
  Workflow admission을 같은 safe reason으로 failed 처리한다. Adapter/storage unavailable은
  terminalize하지 않고 retryable하게 남긴다.
- Rationale: 오류의 치유 가능성에 따른 분류가 영구 lease와 premature terminalization을 함께
  방지하고 exact retry가 기존 Turn을 복구할 수 있게 한다.
- Affected files: Gateway/Worker conversation composition, public runtime, execution orchestration,
  provider usage adapter 및 관련 Memory/Gateway/Workflow 테스트.
- Follow-up review: Workflow 월 예산의 pre-dispatch/provider-before-send 이중 fence와 차단
  audit는 MBA-385에서 atomic reservation/commit 가능성을 포함해 구현한다. 그 변경도 usage
  intent와 provider-start no-replay 순서를 유지한다.

### Public root transport와 frozen runtime 계약 수렴

- Context: root public-run은 Memory-OFF legacy 호출과 Conversation 호출을 같은 path에서
  처리한다. Endpoint가 body를 읽은 뒤에만 Conversation marker를 설정하면 malformed JSON,
  non-object body와 OPTIONS가 전역 credentialed CORS를 먼저 상속한다. 동시에 frozen Start
  `max_length`, context 4,096 token 상한, run rate baseline과 terminal exact replay status가
  validator/application/deployment/API adapter 사이에서 서로 다르게 구현돼 있었다.
- Options considered: middleware가 request body를 buffer/parse, Conversation 전용 root path를
  즉시 분리, 기존 path에서 필수 transport header로 사전 분류하고 endpoint marker를 보강하는
  방식을 검토했다.
- Final decision: outer middleware는 root path의 `Conversation` authorization,
  `Idempotency-Key`, preflight requested authorization/idempotency header를 body parsing 전
  Conversation transport signal로 사용한다. Signal이 없는 Memory-OFF legacy 호출의 CORS는
  유지하고 valid envelope은 endpoint marker로 확정한다. Frozen Start `max_length`는 canonical
  runtime binding에 보존해 UTF-8 byte 상한으로 admission 전에 적용한다. Context 상한은
  4,096, 60초 run rate 기본값은 deployment 120/organization 600/network 60/grant 20으로
  requirements, composition과 Docker/Helm에 동일하게 고정한다. Non-terminal run은 `202`,
  completed/failed/cancelled exact retry는 새 publish 없이 approved display 또는 bounded safe
  failure를 담은 `200` turn projection을 반환한다.
- Rationale: header discriminator는 ASGI body를 이중 소비하지 않으면서 framework validation과
  preflight 이전에 secret-bearing Conversation 응답을 sanitize한다. Canonical frozen binding과
  배포 기본값을 단일 계약에 맞추면 validation 결과 손실과 문서/코드 drift를 제거한다.
- Affected files: shared runtime validator, Memory public binding/runtime/admission, Gateway run/CORS
  composition, Docker/Helm defaults, API/component/test 문서와 관련 tests.
- Follow-up review: external JavaScript SDK나 별도 cross-origin surface가 필요하면 현재 root
  discriminator를 확장하지 않고 별도 path, exact origin/preflight와 credentialless 계약을 새
  ADR로 승인한다.

### Workflow admission terminal retention과 deployment snapshot

- Context: `conversation_workflow_execution_admissions.deployment_id`의 `RESTRICT` FK는 admission
  row 하나만 있어도 Deployment 삭제를 막았고 terminal row에는 expiry나 bounded cleanup이
  없어 운영 데이터가 무기한 증가했다. 반면 별도 durable execution journal은 MBA-386으로
  분리돼 현재 PR에서 다시 도입할 수 없다.
- Options considered: Deployment 삭제 시 admission cascade, `RESTRICT` 유지와 수동 운영 삭제,
  deployment identity를 immutable snapshot으로 유지하되 control-row FK를 제거하고 terminal
  admission만 bounded retention하는 방식을 검토했다.
- Final decision: admission의 deployment ID/version은 실행 당시 immutable snapshot으로 저장하고
  `workflow_deployments` FK를 두지 않는다. Admitted/leased row는 expiry가 없고 terminal 전이는
  canonical terminal 전이 시각에서 8일 뒤 `retention_expires_at`을 한 번 고정하며 exact finish replay가 이를
  연장하지 않는다. DB constraint가 non-terminal NULL과 terminal `expiry > terminal_at`을
  강제한다. Versioned Conversation queue의 1분 periodic task는 `(retention_expires_at, id)`
  순서로 최대 500개 expired terminal row를 `FOR UPDATE SKIP LOCKED`로 삭제한다.
- Rationale: snapshot은 과거 execution correlation을 유지하면서 mutable control row lifecycle을
  막지 않는다. 8일은 public conversation/purge의 최대 운영 접근 기간과 맞고 bounded batch와
  skip-locked claim은 active execution이나 동시 worker를 방해하지 않는다.
- Affected files: Workflow admission domain/repository/task, admission model와 additive migration,
  Memory schema readiness, shared Celery route/schedule, data model/component/test 문서와 tests.
- Follow-up review: MBA-386이 execution journal/운영 projection을 도입할 때 현재 admission을
  장기 history로 재사용하지 않고 별도 조회 권한, retention과 migration을 승인한다. Retention
  수치를 변경할 때는 public purge/access 기간과 운영 증거 요구를 함께 재검토한다.


## 결과

- Public conversation의 raw content는 Memory content store와 provider process-local request
  밖으로 복제되지 않는다.
- Duplicate delivery와 crash window가 Turn, Workflow admission, provider usage와 final entry의
  cardinality를 늘리지 않는다.
- 첫 slice 밖 graph를 묵시적으로 지원하지 않으므로 이후 topology/provenance 확장이
  기존 보안 경계를 약화하지 않는다.
- Memory marker와 usage ledger가 충돌할 때 replay 권위는 usage ledger 하나로 수렴한다.
