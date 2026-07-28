# Conversation Memory Component Specification

Status: Implemented Public client-held history; authenticated durable Memory follow-up

## Architecture

Public Chatbot은 별도 Memory aggregate를 생성하지 않는 client-held history path다.

```text
Embed Chat React memory + no-store public deployment info
  -> POST /run-public/{slug}/chat { inputs, deployment_version, conversation.history }
  -> Gateway version precondition + shape/turn/token validation
  -> Workflow task (opaque history reference only)
  -> LLMNode leaf content single sanitation
  -> bounded RAG query context + untrusted provider history block
  -> HTTP response
```

Gateway는 history를 권위 데이터로 사용하지 않고 20 turn/4,096 token으로 bound한다. LLMNode는 각 history content를 한 번 정제한 canonical projection만 RAG 검색과 provider message 조립에 공유한다. RAG 검색어에는 현재 질문을 우선하고 남는 1,000자 안에서 가장 최근 완료 turn부터 포함하며, history는 Knowledge candidate·authorization 판정에 사용하지 않는다. WorkflowLogger는 이 surface에서 run/node/trace content persistence를 닫고 metadata만 남긴다. Public Conversation lifecycle router와 public persistence composition은 active API에 연결하지 않는다.

아래 Session aggregate, repository, lease, purge와 summary component는 authenticated internal Chatbot 후속 target이다.

## Session Surface Composition

| Surface | 현재 상태 | Conversation state |
| --- | --- | --- |
| Public Chatbot | MBA-318 구현 | Client React memory, 매 요청 bounded history 전달 |
| Authenticated Internal Chatbot | 후속 이슈 | 서버 durable Memory aggregate |
| Workflow Editor test / batch trigger | 미지원 | 자동 session 생성 없음 |

## Target Package Direction

```text
apps/memory/
  domain/
    conversation.py
    errors.py
  application/
    ports.py
    lifecycle.py
    dispatch.py
    public_lifecycle.py  # dormant legacy foundation; active Public route에 연결하지 않음
  adapters/
    admission.py
    audit.py
    security.py
    persistence/
      repository.py
      readiness.py

apps/gateway/composition/memory.py
apps/workflow_engine/composition/memory.py
```

MBA-316이 구현한 dormant package와 후속 composition root의 경계를 함께 표시한 shape다. `apps/memory/`의 위 파일은 실제 aggregate/use case/port/persistence adapter이며, 두 composition root와 authorization/summarization/usage/audit adapter는 아직 생성하거나 production traffic에 연결하지 않는다. 후속 기능도 실제 use case 없이 빈 package를 먼저 만들지 않는다.

SQLAlchemy persistence model은 기존 Alembic metadata registry와의 호환을 위해 `apps/shared/db/models/`에 둘 수 있다. Memory persistence adapter 외 production code가 해당 model을 직접 query/mutate해서는 안 된다.

## Domain Model

### Aggregate Boundaries

다음 객체는 bounded aggregate root다.

| Aggregate root | Transaction/CAS boundary | 다른 root 연결 |
| --- | --- | --- |
| `ConversationSession` | Authenticated lifecycle, active turn claim과 session revisions | Opaque turn ID |
| `ConversationTurn` | Request idempotency, dispatch/attempt와 terminal transition | Session ID, entry ID |
| `ConversationAccessGrant` | Dormant legacy Public foundation, active runtime 사용 금지 | Session/deployment ID |
| `TurnDispatchJob` | Claim/publish/ack/reconcile | Turn/session ID |
| `ConversationPurgeJob` | Tombstone 이후 bounded batch purge/retry/terminal receipt | Session ID |
| `SummaryGenerationJob` | Lease fencing, budget/provider/summary/usage state | Session/channel/source revision |
| `MemoryContextLease` | Issue/single-attempt claim/invalidate/expiry | Session/turn/node/context plan |
| `MemoryContextProviderAttempt` | Lease claim/provider-start/outcome/reconcile | Lease/context handle/node invocation |

`ConversationMemoryEntry`는 content가 immutable인 append-oriented record이며 provisional/approved/rejected status만 CAS로 전이한다. `ConversationMemorySummary`는 source revision에 대한 materialized projection, dependency는 normalized relation으로 관리한다. 구현이 이들을 별도 aggregate root로 승격할 수 있지만 Session aggregate object graph에 전체 turn/entry/summary collection을 적재해서는 안 된다. Aggregate 사이에는 opaque ID와 immutable/revision snapshot만 전달한다.

Persistence FK는 aggregate ID만 단독 신뢰하지 않고 가능한 모든 Memory-owned relation에 organization/session scope를 포함한다. Active Turn과 Purge tombstone처럼 대상 삭제 시 nullable reference만 `SET NULL`로 보존해야 하는 relation은 단일 `SET NULL` FK와 deferred composite scope FK를 함께 사용해 삭제 보존과 tenant 무결성을 동시에 유지한다.

`StartTurn`은 single-active-turn claim, Turn, TurnDispatchJob과 required outbox가 함께 존재해야 하므로 명시적 cross-aggregate UoW다. `CompleteTurn`은 Turn terminal 전이, Session active-turn 해제/content revision, final entry/projection 승격과 required outbox를 같은 UoW에 둔다. Authenticated internal Reset의 old close + new session, Delete tombstone + purge job/audit outbox도 하나의 lifecycle UoW다. 이 예외를 generic multi-aggregate transaction service로 확장하지 않는다. Summary 상태 전이는 각 root의 version/CAS와 process manager로 조정한다.

### ConversationSession Aggregate

소유:

- organization/app/workflow, deployment ID와 immutable version 또는 snapshot hash binding
- conversation mapping/Memory policy version과 authenticated execution subject binding
- memory contract와 storage generation
- active/closed/delete-pending/deleted lifecycle
- lifecycle/content revision
- active turn reference
- retention/expiry

주요 불변식:

1. Active session만 turn을 시작할 수 있다.
2. 초기 policy에서는 session당 active turn은 최대 하나다.
3. Close/reset/delete 이후 pending completion은 terminal session을 변경할 수 없다.
4. Lifecycle revision은 content/summary 변경으로 증가하지 않는다.
5. Content revision은 completed turn/approved projection에만 증가한다.
6. Organization/audience/deployment version/mapping/Memory policy/storage generation은 session lifecycle 중 바뀌지 않는다.
7. Active deployment pointer 변경은 기존 session을 자동 rebind하지 않는다.

### ConversationAccessGrant

Public Access Grant는 ADR-0074 이후 active component가 아니다. 기존 model/adapter는 dormant foundation으로만 남고 API router와 Public runtime에서 생성·조회하지 않는다. Authenticated internal Chatbot은 Access Grant 대신 current authentication/authorization과 session subject binding을 사용한다.

### ConversationTurn

- canonical request ID/fingerprint
- execution ID/latest attempt ID
- sequence/turn version
- started lifecycle revision
- pending_dispatch/queued/running/completed/failed/cancelled state
- user/final assistant entry reference
- safe failure reason

User turn은 StartTurn에서 한 번 기록하고 assistant turn은 mapped final output을 CompleteTurn에서 한 번 기록한다. 개별 LLM node가 user/final assistant turn을 중복 저장하지 않는다.

### TurnDispatchJob

Pending turn과 같은 transaction에서 생성되는 durable outbox/process record다.

- dispatch id와 turn/session opaque reference
- task contract/storage generation과 minimum worker capability
- pending/claimed/published/acknowledged/reconcile-required/terminal 상태
- monotonic claim generation, claim owner와 deadline
- bounded publish attempt, next-attempt timestamp와 safe failure reason
- broker message ID/correlation reference

Dispatcher는 skip-locked 또는 동등한 atomic claim을 사용한다. Publish 성공 응답을 잃은 경우 같은 dispatch ID로 재발행할 수 있으며 Worker의 durable StartExecution admission이 중복을 제거한다. Admission 전에는 같은 dispatch를 재발행할 수 있지만 admission 이후 heartbeat/outcome이 불명확하면 Workflow execution reconciliation에 위임하고 Memory가 임의 workflow node side effect를 직접 재실행하지 않는다. Reconciliation 결과가 bounded deadline 안에 없으면 turn을 safe terminal failure로 닫아 session 점유를 해제한다.

Workflow execution admission과 lease/heartbeat는 Workflow domain이 소유한다. Memory는 admission reference와 safe state projection만 보존한다. Admission 성공 뒤 Memory acknowledgement가 유실되면 dispatch reconciler가 dispatch ID로 Workflow admission lookup port를 호출해 복구한다.

### ConversationMemoryEntry

Raw trace 전체가 아니라 승인된 redacted/bounded projection이다.

- entry type: user turn, assistant turn, explicit node projection
- producer node/channel
- display content와 model projection의 분리 가능성
- sensitivity/expiry/invalidation
- provisional/approved/rejected lifecycle
- idempotency key
- Data Dependency references

Display와 model projection은 서로 다른 redaction 결과일 수 있으므로 암호문뿐 아니라 key/format version, digest와 plaintext byte length도 projection별 envelope로 분리한다. 둘 중 하나 이상이 있어야 하며 각 projection은 독립적으로 16 KiB 상한을 적용한다. Domain과 예외의 `repr`에는 두 암호문을 포함하지 않는다.

### ConversationMemorySummary

Source entry에서 재생성 가능한 projection이다.

- cache identity: session + channel + source revision + policy version + summarizer version
- source sequence range
- redacted summary
- aggregate dependency
- model/usage safe reference
- expiry/invalidation

Source entry보다 오래 보존하거나 current authorization을 확장할 수 없다.

### MemoryDataDependency

- source kind
- organization
- canonical resource/version
- authorization-safe reference
- sensitivity
- source/resource/policy와 authorization decision revision

V1에서는 content에 영향을 준 dependency를 모두 필수로 취급하며 optional 의미를 저장하거나 평가하지 않는다. Dependency는 reverse invalidation과 tenant constraint를 지원하는 normalized relation을 기본 권고로 한다. Provider-specific safe metadata만 bounded JSONB 후보로 둔다.

### RuntimeDataDependencyEnvelope

Workflow Runtime의 모든 content-bearing node result는 bounded dependency 집합과 server-derived completeness marker를 함께 전달한다. 외부/private source 영향이 없음을 producer가 확인한 explicit complete empty envelope은 유효하지만 missing/unknown envelope은 empty가 아니다.

| Producer | 발급/전파 책임 |
| --- | --- |
| Knowledge retrieval | Knowledge adapter가 KB/document version, sensitivity와 authorization-safe reference 발급 |
| Connector/tool | 승인된 adapter가 connector resource/item revision, source ACL/egress policy revision 발급 |
| Subworkflow | Workflow Runtime이 target deployment version과 child envelope 합집합 반환 |
| LLM | Prompt input/Memory Context/retrieval/tool dependency 합집합 상속 |
| Transform/code | 모든 value input dependency 합집합을 보존하고 canonical dependency를 발급·삭제하지 않음 |
| Condition/Switch | Predicate dependency와 선택 route를 active control context에 추가. 선택된 상수 output도 control dependency 상속 |
| Loop | Iterable/bound/continue/termination 판단 dependency를 body와 loop 결과의 active control context에 추가 |
| System/privacy policy | Policy owner가 classification/redaction policy revision 발급 |

Runtime은 값 dependency와 활성 control dependency를 구분해 계산하되 V1 canonical envelope에는 둘의 필수 합집합을 기록한다. 선택되지 않은 branch의 값 dependency는 합산하지 않는다. Client 또는 arbitrary node는 canonical dependency를 만들 수 없다. Code/custom result가 lineage를 보존하지 못하면 public-only/non-sensitive임을 server policy가 증명하지 않는 한 private/sensitive Memory write를 거부한다.

### SummaryGenerationJob

Provider 호출을 DB transaction 안에 넣지 않기 위한 durable process manager다.

```text
lease_acquired
  -> budget_reserved
  -> provider_started
  -> provider_succeeded
  -> summary_committed
  -> usage_committed

non-terminal -> reconcile_required | failed
```

Provider 성공 후 summary CAS 실패는 content를 저장하지 않되 usage를 정산한다. Summary commit 후 usage commit 실패는 provider를 재호출하지 않고 reconciliation한다.

Lease를 재획득할 때마다 `lease_generation`을 증가시킨다. Summary projection CAS와 generation state 전이는 현재 generation과 source revision을 함께 검증한다. Generation job은 ProviderExecutionCapability와 capability-bound reservation reference를 보존한다. 만료된 이전 owner의 늦은 완료는 summary content를 commit하지 않는다. 다만 provider 호출이 실제 발생했다면 provider attempt idempotency key와 reservation reference를 reconciliation에 전달해 actual usage를 정확히 한 번 정산한다.

### MemoryContextLease

BuildMemoryContext 결과를 main provider가 소비할 때 사용하는 short-lived authorization lease다.

- session/execution subject 또는 audience/node invocation binding
- lifecycle/content revision
- authorization decision revision set hash
- ProviderExecutionCapability reference
- issue/claim generation/claim deadline/invalidation/expiry
- opaque ContextMaterializationPlan handle

Context handle은 raw content를 복제 저장하지 않는 short-lived `ContextMaterializationPlan`을 가리킨다. Plan에는 ordered entry/summary reference, policy version, server-keyed content digest, lifecycle/content/source revision, authorization decision revision set과 expiry만 저장한다. Digest는 client/trace/metric에 노출하지 않는다.

Permission/source invalidation event는 plan과 미사용 또는 claimed-not-started lease를 무효화한다. Build 단계는 raw context를 caller에 반환하지 않는다. Main provider adapter가 lease를 server-issued provider attempt ID와 matching ProviderExecutionCapability로 claim하고 current authorization을 재검증한 같은 trusted operation 안에서 Memory store의 projection을 materialize한다. Claim result는 materialized context와 정확히 대응하는 server-derived RuntimeDataDependencyEnvelope를 함께 반환해 LLM output provenance에 합산한다. Lease는 완전한 distributed transaction을 의미하지 않고 stale authorization window를 제한한다.

### MemoryContextProviderAttempt

- server-issued provider attempt ID와 node invocation binding
- lease/context plan reference와 claim generation/deadline
- ProviderExecutionCapability identity/revision과 purpose
- `claimed`, `provider_started`, `succeeded`, `failed`, `outcome_unknown`, `reconciled` 상태
- provider request idempotency/correlation safe reference와 usage result

같은 attempt의 claim retry는 같은 plan/version에 대해 idempotent하고 다른 attempt는 활성 claim을 탈취할 수 없다. Provider adapter는 outbound 호출 직전에 `provider_started`를 durable하게 기록하며 marker commit이 실패하면 provider를 호출하지 않는다. Claim 뒤 start 전 crash는 claim expiry 후 current authorization을 다시 평가해 새 lease/attempt를 발급할 수 있다. `provider_started` 이후 crash/timeout은 실제 호출 여부를 낙관하지 않고 `outcome_unknown`으로 두며 provider를 자동 재호출하지 않는다.

Main provider usage와 `llm.call` 감사는 기존 LLM/Workflow 경계가 소유한다. MemoryContextProviderAttempt는 해당 safe usage/correlation reference만 보존하고 별도 비용이나 중복 `llm.call` AuditLog를 만들지 않는다.

### ConversationPurgeJob

Authenticated internal Delete/retention을 비동기 physical erasure로 수렴시키는 후속 process record다.

- organization/session과 authenticated subject/deployment scope snapshot
- requested/running/completed/completed_with_hold/terminal_failure 상태
- claim generation, retry deadline, safe failure code
- content store/cache/backup-export erasure marker
- legal-hold compliance handoff reference

Public client-held history에는 purge job, receipt, secret replay 또는 legal-hold lifecycle이 없다. MBA-317의 Public grant/idempotency/replay/purge foundation은 기존 데이터 정리와 향후 설계 참고를 위해 dormant 상태로 보존하지만 ADR-0074의 route/runtime에 연결하지 않는다.

## Application Use Cases

### ResolveOrCreateSession

- canonical authenticated runtime identity와 current organization/deployment 권한 검증
- execution subject binding
- deployment ID/version 또는 snapshot hash와 conversation mapping/Memory policy version 고정
- retention policy 적용
- contract/storage generation 고정

기존 session과 요청 deployment binding이 다르면 active deployment를 따라 자동 rebind하지 않고 typed version conflict와 명시적 new-session 흐름을 사용한다. Public은 이 use case를 호출하지 않는다.

### StartTurn

- lifecycle/active-turn 검증
- request idempotency와 fingerprint conflict 처리
- sequence 할당
- user display/memory projection redaction
- pending turn 생성
- 같은 UoW에서 TurnDispatchJob과 required audit/outbox 저장

Commit 뒤 HTTP adapter가 Celery에 직접 publish하지 않는다. Dispatcher가 durable job을 claim/publish하고 HTTP wait budget 안에 완료되지 않으면 caller는 accepted turn 상태를 받는다.

### Dispatch Processing

- `ClaimTurnDispatch`: current fencing generation으로 pending/retry job claim
- `MarkTurnDispatchPublished`: broker message reference와 publish outcome 기록
- `ObserveWorkflowAdmission`: Workflow domain의 durable admission reference를 검증하고 acknowledged/queued 상태 반영
- `ObserveTurnExecutionState`: Workflow-owned running/terminal state의 safe projection 반영
- `ReconcileTurnDispatch`: publish ambiguity와 acknowledgement 유실을 dispatch/admission lookup으로 복구

Celery/persistence adapter는 위 command를 호출할 뿐 state transition을 직접 결정하거나 commit하지 않는다. Workflow `AdmitExecution(dispatch_id)`은 별도 Workflow application contract이며 Memory command가 execution lease를 소유하지 않는다.

### BuildMemoryContext

1. Node config와 session/channel 검증
2. Bounded candidate snapshot 조회
3. Approved/completed/non-expired entry 선택
4. Source authorization bulk 평가
5. Token/turn budget 적용
6. Window 또는 summary strategy 실행
7. ContextMaterializationPlan handle 생성
8. Context authorization lease 발급
9. Safe decision/usage result 반환

Window-only path는 read-only다. Summary path는 generation job, budget reservation, provider call, projection/usage reconciliation side effect를 포함하므로 순수 query가 아니다.

### AppendNodeProjection

- Configured producer/channel allowlist 검증
- Bounded output/redaction/provenance 검증
- Turn version과 idempotency를 적용해 provisional projection 저장
- User/final assistant turn과 중복되지 않게 저장

중간 projection은 현재 turn의 작업 메모리에는 사용할 수 있지만 cross-turn candidate에는 포함하지 않는다. `CompleteTurn`이 성공하면 config와 final provenance에 따라 approved로 승격한다. Turn failed/cancelled 또는 lifecycle mismatch이면 rejected/expired 처리하고 content revision을 증가시키지 않는다.

### CompleteTurn

- Turn version/start lifecycle 검증
- Final output mapping 검증
- RuntimeDataDependencyEnvelope와 completeness 검증
- Completed/failed/cancelled 전이
- Final assistant entry와 content revision commit
- 허용된 provisional projection 승격과 나머지 폐기
- Audit/outbox를 같은 transaction 또는 durable outbox로 기록

위 변경은 하나의 cross-aggregate UnitOfWork에서 commit/rollback한다. Mapped user/final assistant write는 conversational surface의 required contract다. CompleteTurn이 실패하면 성공 또는 `memory_status=applied`를 반환하지 않는다. Workflow의 durable execution result가 이미 있으면 reconciliation/retry는 provider나 arbitrary node를 재실행하지 않고 CompleteTurn만 idempotent하게 재시도한다.

### Lifecycle Use Cases

Authenticated internal surface만 다음 lifecycle command를 사용한다.

- Create/new
- Close
- Reset
- Delete/tombstone
- Retention expiry
- Durable purge
- Source invalidation/revalidation

모든 mutation은 현재 authenticated subject, organization, deployment/session scope와 idempotency fingerprint를 transaction 안에서 다시 검증한다. Close는 runtime write를 닫되 허용된 transcript read와 privacy delete를 막지 않는다. Reset은 기존 session을 close하고 새 session을 만들며, Delete는 접근을 즉시 차단한 뒤 purge process를 시작한다. Public reset/new conversation은 Client local history 폐기이므로 이 lifecycle UoW와 audit action을 만들지 않는다.

## Ports And Adapters

### Repository And UnitOfWork

Capability 중심 API를 사용하고 generic table CRUD를 노출하지 않는다.

- get session/turn/access grant
- append turn if lifecycle/active-turn matches
- append turn and dispatch job atomically
- complete turn/session/entry/projection/outbox atomically if versions match
- list bounded candidate snapshots
- append entry if absent
- acquire/observe summary generation job
- compare-and-swap summary projection
- invalidate by source/version
- close/delete/expire/purge
- claim/publish/reconcile dispatch job with fencing generation

Repository adapter는 commit/rollback을 소유하지 않는다. Mutation use case 또는 UnitOfWork가 session lock, entry write와 audit/outbox를 조율한다.

### Secret Response Replay Store

Public client-held history에는 secret/grant response replay store가 없다. 기존 adapter는 신규 Public row를 만들지 않으며, authenticated internal lifecycle이 replay persistence를 요구하면 별도 계약으로 검토한다.

### Source Authorization

Source kind별 domain capability를 호출한다.

- Organization membership/subject state
- Knowledge `use`, source ACL와 lifecycle
- Connector/tool resource access
- Subworkflow/app/deployment scope
- System policy/version validity

Memory가 permission row, connector ACL 또는 deployment audience 규칙을 직접 조합하지 않는다.

Bulk authorization result는 dependency별 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 반환한다. Source ACL이 있는 resource는 ACL revision을 decision revision에 반영한다. Authenticated internal subject와 revision은 Gateway/source domain의 current authorization에서 가져온다. Required field가 없거나 adapter timeout/unknown이면 fail-closed 한다. Memory가 source domain의 revision을 자체 합성하지 않는다.

### Runtime Provenance

Knowledge/tool/connector/subworkflow adapter는 node output과 함께 server-derived `RuntimeDataDependencyEnvelope`를 제공한다. Workflow Runtime은 transform/code/LLM/final output에서 값 dependency와 결과를 선택한 활성 control dependency의 합집합을 전파한다. V1은 optional dependency를 지원하지 않는다.

Code/custom node가 dependency를 보존하지 못하면 `provenance_incomplete`로 표시한다. Private/sensitive result는 fail-closed하고 public-only/non-sensitive임을 server policy가 증명한 projection만 별도 allow policy 아래 저장한다.

### Summary Execution Policy And Summarizer

LLM Credentials domain의 authoritative port가 발급한 opaque `ProviderExecutionCapability` identity/revision을 사용한다. 상세 scope, credential principal과 permission decision revision은 [LLM Credentials API Spec](../llm-credentials/api_spec.md#target-provider-execution-capability-contract)이 소유한다. 초기 summary policy는 `inherit_node`만 지원한다. Summarizer adapter는 승인된 capability와 bounded prompt로 provider를 호출하고 normalized usage/cost를 반환한다. Adapter가 model fallback, 조직 기본 credential 또는 permission policy를 독자 결정하지 않는다.

Summary generation lease는 source authorization decision revision set에 binding한다. Lease owner는 summary provider 호출 직전에 current authorization과 lease validity를 재검증하며, revoke/expiry/unknown이면 raw history를 provider에 보내지 않는다.

### Privacy Classification And Redaction

Shared privacy boundary는 bounded content에 대해 classification, redacted projection, policy version과 safe reason을 반환한다. Memory는 source-specific PII/secret detector를 복제하지 않고 결과와 policy version만 보존한다. Detector unavailable 또는 unsupported sensitive content는 fail-closed 한다.

### Budget And Usage

- Opaque ProviderExecutionCapability identity/revision의 Budget-required purpose/pricing/cap binding에 따른 예상 비용 reserve
- actual usage commit
- 명확한 미호출/실패 release
- unknown provider outcome reconcile
- billing principal/organization/workflow/deployment/purpose attribution

기존 Budget adapter가 atomic reservation을 제공하지 않으면 summary strategy는 `window`로 제한한다. Reservation capability를 지원한다고 선언한 adapter만 `window_then_summary` composition에 주입할 수 있다.

Price estimate가 unavailable, invalid 또는 unknown 때문에 zero이면 reservation을 거부하고 `budget.price_unavailable`을 반환한다. Summary provider는 호출하지 않으며 Memory/Budget adapter가 임의 가격 fallback을 만들지 않는다.

### Audit And Outbox

Public client-held history는 Conversation session/grant/lifecycle audit을 만들지 않는다. 기존 `workflow.execute`와 provider usage에는 content-free deployment/workflow/run ID, status, duration, token/cost 같은 safe metadata만 허용하며 history, current input, prompt와 completion을 넣지 않는다.

Authenticated internal lifecycle은 raw Memory content/token/source를 제외한 action, status와 safe reason을 transaction-bound audit 또는 durable outbox로 기록한다. Actor는 실제 authenticated user이며 execution/credential/billing principal이나 app/deployment owner로 대체하지 않는다. 비동기 physical purge/compliance completion만 system actor를 사용한다.

## Runtime Sequence (Authenticated Internal Target)

```text
Gateway resolves canonical execution subject/audience, principal roles and version-pinned session
  -> Memory.StartTurn atomically stores pending turn + dispatch job
  -> Dispatcher claims job and publishes versioned task
  -> Worker validates contract/storage/capability before side effects
  -> Workflow admission deduplicates dispatch/execution attempt
  -> LLM node evaluates versioned MemoryConfig
  -> Workflow creates server-issued provider attempt reference without starting provider effect
  -> LLM Credentials resolves invocation/admission/attempt-bound main-generation ProviderExecutionCapability
  -> Memory.BuildMemoryContext with capability reference
       -> candidate snapshot
       -> current source authorization
       -> optional summary generation job
       -> materialization plan handle + context lease
  -> Provider adapter claims lease with provider attempt + ProviderExecutionCapability
       -> revalidates authorization and materializes raw context
       -> durably marks provider_started immediately before outbound call
  -> LLM node consumes untrusted Memory Context
  -> Workflow propagates RuntimeDataDependencyEnvelope
  -> optional explicit node projection
  -> Workflow maps final assistant output
  -> Memory.CompleteTurn
  -> Log System observes safe event
```

## Client Components

### LLM Node Detail

- Memory enable toggle
- Source/channel and selected node allowlist
- Turn/token limit
- Window/summary strategy
- Failure policy
- Cost/privacy notice

Memory controls are saved in graph/deployment snapshot. Canvas local state만으로 runtime behavior를 결정하지 않는다.

### Conversation Controls

- Client는 화면의 완료된 user/assistant pair만 골라 최신 20 turn을 전송한다.
- welcome, error, pending user message와 citation UI metadata는 전송하지 않는다.
- 완료된 assistant 응답은 화면에는 표시하되 Unicode scalar 32,768-character 상한을 넘거나 invalid surrogate를 포함하면 다음 요청 history에서 제외한다. 이 pair를 제외해 이후 정상 turn이 계속 전송되게 한다.
- Client는 history JSON의 UTF-8 크기가 131,072 bytes 이하가 될 때까지 가장 오래된 완료 pair를 제거한다.
- 새 대화/reset은 local message state를 비운다.
- localStorage/sessionStorage에 대화 원문이나 conversation ID를 저장하지 않는다.
- server transcript/close/delete/purge 상태 UI는 Public mode에 표시하지 않는다.

### Abuse Protection Adapter

Public Chatbot은 기존 deployment/network/budget admission을 사용하며 history를 identity나 grant scope로 사용하지 않는다. 별도 Public session-create/grant limiter는 없다.

### Transcript

Public transcript는 현재 Client가 렌더링하는 local messages이며 서버 조회 API가 없다. Authenticated internal transcript component는 후속 durable surface에서 별도 권한 계약으로 구현한다.

## Observability

허용:

- session/turn/entry safe opaque reference
- Public stateless 또는 authenticated internal surface kind
- action/status/safe reason
- latency/cache hit/usage purpose
- bucketed excluded dependency count
- generation/reconciliation state

금지:

- raw Memory/transcript/prompt
- public access token/hash
- credential/provider raw error
- private source title/path/url
- exact denied source count
- arbitrary user input in metric label

## Migration Boundary

- New graph는 versioned node Memory config와 conversational mapping을 사용한다.
- Public legacy `memory_mode`/`conversation_id`는 거부한다. Authenticated internal cutover에서만 versioned compatibility를 별도 정의하며 log-derived history는 기본 context에서 제외한다.
- Deployment preflight는 Worker capability와 contract/storage generation을 검증한다.
- Same session은 하나의 reader/writer generation만 사용한다.
- Same session은 생성 시 deployment version/snapshot과 conversation mapping/Memory policy version에 고정하며 active deployment 변경에 자동 rebind하지 않는다.
- Target task는 versioned queue 또는 capability 전용 worker pool에서만 소비하고 개별 Worker가 envelope을 재검증한다.
- Global canvas toggle, Chatbot all-node forced ON과 reserved business input는 migration gate 이후 제거한다.

## MBA-318 Public Consumer And Rollout Components

- 공개 Chatbot 배포 모달은 graph의 top-level LLM 목록에서 “대화 기록을 사용할 LLM 노드” 하나를 선택한다. LLM이 하나면 명시적으로 그 값을 표시한 상태로 시작하고, 여러 개면 선택 전 배포를 막는다.
- Client 선택은 `public_chat_conversation.v1` deployment config로 preflight와 create에 동일하게 전송한다. Canvas 임시 상태만으로 runtime consumer를 정하지 않는다.
- Gateway와 Worker는 canonical snapshot location을 검증하며 LLMNode는 자신이 지정 consumer일 때만 history projection을 만든다. 다른 classifier/router/provider node는 현재 입력만 사용한다.
- public info capability가 `client_history_v1`이면 Embed Chat은 `/chat`과 client-held history를 사용한다.
- capability가 `legacy_v0`이거나 필드가 없으면 Embed Chat은 `memory_mode`와 `conversation_id` 없이 rollout 호환 root 요청을 사용한다.
- Embed Chat은 두 경로 모두 public info의 `version`을 `deployment_version`으로 보내며, active version conflict에서는 info를 no-store로 갱신하고 이전 history를 폐기한 뒤 현재 입력을 한 번만 재시도한다.
- 새 Gateway의 compatibility adapter는 root 요청을 무상태로 실행하며 server Memory identity를 만들지 않는다.
- strict rollout 전환 뒤에는 모든 active public Chatbot deployment가 versioned consumer config를 가져야 한다.
- Public audit actor는 authorization subject와 별도다. UI는 history나 actor marker를 권한·신원으로 표시하지 않는다.
- Gateway ASGI middleware는 Public root와 `/chat` request lifetime을 body buffering 전에 생성한다. Gateway는 raw history를 그 deadline의 남은 시간 이하 TTL인 일회성 Redis key에 두고 broker에는 opaque reference만 전달한다. Admission, Redis I/O, task expiry, result polling과 Worker는 같은 absolute deadline을 사용한다. Worker는 queued raw history를 거부하고 atomic consume 뒤에만 invocation-local 원문을 만든다. Store unavailable은 소비 전 bounded retry를 사용하지만 invalid/missing/corrupt 또는 소비 뒤 오류는 provider replay 없이 닫는다.
- Public transient task는 `workflow.execute_public_chat.v1`/`workflow-public-chat-v1` 계약으로만 전달한다. 일반 task는 public marker를 fail-closed하고 새 Worker만 전용 queue를 함께 구독한다.
- Gateway transient store adapter는 async Redis `SET NX EX`를 bounded timeout 안에서 실행한다. Redis가 지연되면 event loop를 점유하지 않고 safe 503으로 종료한다.
- `/chat` adapter는 `deployment_version`을 필수 precondition으로 검증하지만 compatibility root adapter는 rolling 전환을 위해 생략을 허용한다.
- Query embedding application service는 각 model group provider invoke 직전에 전달받은 deadline guard를 실행한다. Deadline 예외는 `safe_no_result` provider 오류 처리에 흡수하지 않는다.
- Compose와 Helm ConfigMap은 Public rollout mode를 Gateway container까지 전달한다.
- Nginx Public `/chat` location은 Gateway lifetime보다 긴 upstream timeout과 Gateway와 동일한 safe 413 응답 계약을 소유한다.
- Helm Ingress template도 600초 absolute lifetime보다 긴 read/send timeout을 렌더링하고 600초 이하 values를 거부한다.
- LLMNode model-routing judge는 Public content-persistence suppression을 learning label보다 먼저 확인한다. Suppressed 실행은 selection과 content-free usage만 유지하고 feature text/vector/hash label write를 건너뛴다.
- Public history transient store의 Worker consume adapter는 요청마다 남은 deadline 이하의 bounded Redis client를 만들고 atomic consume 뒤 connection pool을 정리한다.
- ModelRoutingRuntimeJudge는 optional deadline guard를 최초와 compact retry provider invoke 직전에 호출하며 LLMNode는 공통 Public external-I/O guard를 주입한다.
- 전용 Public task adapter는 unsafe legacy memory control을 canonical row 조회 전에 거부하고 safe false/null sentinel도 WorkflowEngine context로 전달하지 않는다.
