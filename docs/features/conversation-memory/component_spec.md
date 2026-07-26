# Conversation Memory Component Specification

Status: Implemented public lifecycle and initial runtime; advanced follow-up pending

## Architecture

Conversation Memory는 [ADR-0030](../../decisions/ADR-0030-memory-bounded-context.md)의 독립 bounded context이며 [ADR-0033](../../decisions/ADR-0033-conversation-memory-contract-completion.md)의 provenance, capability authority, session surface와 Access Grant V1 보정을 따른다. 초기에는 별도 network service가 아닌 in-process 모듈러 모놀리스 package로 구현한다.

```text
Gateway/Chat Inbound Adapter
  -> Memory application use case
       -> Memory domain aggregate/policy
       -> Repository/UnitOfWork port
       -> Source authorization port
        -> Provider execution capability/egress port
        -> Summary execution policy/summarizer port
        -> Budget/usage port
        -> Privacy classification/redaction port
        -> Audit/outbox port

Workflow Runtime Inbound Adapter
  -> StartTurn / BuildMemoryContext / CompleteTurn
```

FastAPI request/response, Celery task, SQLAlchemy expression와 provider SDK는 Memory domain/application policy 안으로 들어오지 않는다.

## Session Surface Composition

- MBA-317 Gateway composition은 public Chatbot adapter를 Conversation Session create/close/reset/delete/transcript/purge-status port에 연결한다. MBA-318은 root-level `conversation` envelope을 public StartTurn admission과 content-free dispatch publisher에 연결하며 Workflow Engine의 durable admission/lease fence가 실행을 소유한다.
- `PublicConversationCorsBoundaryMiddleware`는 public route prefix의 outer transport boundary를 소유한다. Endpoint 진입 전 dependency/body validation과 router/preflight 오류를 포함한 모든 응답에 `Cache-Control: no-store`, `Referrer-Policy: no-referrer`를 적용하고 전역 `Access-Control-*` header와 `Vary: Origin`을 제거한다.
- Authenticated internal Chatbot adapter는 별도 access policy와 route/CSRF/session namespace 계약이 구현된 뒤 연결하는 후속 target이다.
- Workflow Editor test adapter는 일반 test execution만 수행하고 Conversation Session port를 호출하지 않는다. Editor session은 별도 feature/security contract 전까지 composition allowlist에 등록하지 않는다.
- Schedule, webhook, API batch와 subworkflow가 임의 public/authenticated adapter를 재사용해 session을 만들 수 없다.

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
    public_lifecycle.py
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
| `ConversationSession` | Lifecycle, active turn claim과 session revisions | Opaque turn/grant ID |
| `ConversationTurn` | Request idempotency, dispatch/attempt와 terminal transition | Session ID, entry ID |
| `ConversationAccessGrant` | Issue/reset replacement/revoke/expiry와 verifier hash | Session/deployment ID |
| `TurnDispatchJob` | Claim/publish/ack/reconcile | Turn/session ID |
| `ConversationPurgeJob` | Tombstone 이후 bounded batch purge/retry/terminal receipt | Session ID |
| `SummaryGenerationJob` | Lease fencing, budget/provider/summary/usage state | Session/channel/source revision |
| `MemoryContextLease` | Issue/single-attempt claim/invalidate/expiry | Session/turn/node/context plan |
| `MemoryContextProviderAttempt` | Lease claim/provider-start/outcome/reconcile | Lease/context handle/node invocation |

`ConversationMemoryEntry`는 content가 immutable인 append-oriented record이며 provisional/approved/rejected status만 CAS로 전이한다. `ConversationMemorySummary`는 source revision에 대한 materialized projection, dependency는 normalized relation으로 관리한다. 구현이 이들을 별도 aggregate root로 승격할 수 있지만 Session aggregate object graph에 전체 turn/entry/summary collection을 적재해서는 안 된다. Aggregate 사이에는 opaque ID와 immutable/revision snapshot만 전달한다.

Persistence FK는 aggregate ID만 단독 신뢰하지 않고 가능한 모든 Memory-owned relation에 organization/session scope를 포함한다. Active Turn과 Purge tombstone처럼 대상 삭제 시 nullable reference만 `SET NULL`로 보존해야 하는 relation은 단일 `SET NULL` FK와 deferred composite scope FK를 함께 사용해 삭제 보존과 tenant 무결성을 동시에 유지한다.

`StartTurn`은 single-active-turn claim, Turn, TurnDispatchJob과 required outbox가 함께 존재해야 하므로 명시적 cross-aggregate UoW다. `CompleteTurn`은 Turn terminal 전이, Session active-turn 해제/content revision, final entry/projection 승격과 required outbox를 같은 UoW에 둔다. MBA-317의 Reset old close + old grant revoke + new session/grant/replay, Delete tombstone + grant revoke + purge job/replay/audit outbox도 하나의 lifecycle UoW다. 이 예외를 generic multi-aggregate transaction service로 확장하지 않는다. Summary 상태 전이는 각 root의 version/CAS와 process manager로 조정한다.

### ConversationSession Aggregate

소유:

- organization/app/workflow, deployment ID와 immutable version 또는 snapshot hash binding
- conversation mapping/Memory policy version과 execution subject 또는 public audience binding
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

Public session bearer capability의 server-side hash와 lifecycle을 관리한다.

- raw token 비저장
- session/deployment ID·version/audience binding
- `active`, `transcript_only`, `revoked`, `expired` state
- issue/expire/immediate revoke와 reset replacement
- standalone rotation endpoint, grace window와 rotated grant chain은 V1에서 미지원
- idempotency/replay record reference

Authenticated session은 Access Grant가 아니라 current authentication/authorization과 session subject binding으로 접근한다.

Public lifecycle composition은 명시적 feature activation boundary다. 기본 배포는 비활성이고, 활성화 시 실제 DB introspection으로 필요한 Memory table·column capability를 확인한 뒤 capability verifier, replay encryption, admission HMAC의 독립 key material, 승인된 backup erasure/no-backup mode와 physical purge worker readiness를 startup에서 함께 검증한다. Schema readiness는 특정 Alembic revision이나 현재 head와의 문자열 일치가 아니라 이 surface가 소비하는 capability로 판정한다. Capability와 replay keyring은 active+previous 최대 두 개로 제한하고 새 verifier/ciphertext는 active key만 사용한다. 기존 grant/receipt와 bounded replay ciphertext는 stored key version에 맞는 previous key로 각각 원래 expiry/TTL까지만 검증·복호화한다. Keyring 안과 세 용도 전체에서 하나의 material을 재사용하면 fail-closed한다. Schema introspection 실패, 누락되거나 재사용된 설정과 worker 미준비를 요청 시점의 임시 adapter 오류로 늦추지 않는다. Network admission은 Password Login·Connector와 같은 trusted-proxy resolver를 사용한다. 설정된 trusted proxy peer에서만 forwarded chain을 해석하고 direct/untrusted peer는 transport address를 canonical network로 정규화하며 unknown identity는 mutation 전에 fail-closed한다.

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

Delete tombstone 뒤 content-bearing record를 지우고 operational record를 content-free bounded 상태로 정리하는 durable process다.

- purge request/session opaque reference
- organization, deployment ID/version과 audience의 durable scope snapshot. Session FK가 `SET NULL`된 뒤에도 public receipt 검증에 사용
- pending/running/completed/completed_with_hold/retryable_failure/terminal_failure 상태
- monotonic claim generation, cursor, attempt와 next-attempt timestamp
- legal-hold/backup erasure policy reference와 safe terminal reason

| Record class | 처리 |
| --- | --- |
| Content-bearing | Turn content, final/provisional Entry·projection, Summary, sensitive dependency reference, raw/materialized context cache·plan, transcript/result와 conversation access-token replay ciphertext를 물리 삭제 또는 irreversible erasure |
| Session/access | Session subject/audience binding과 Access Grant verifier/source row를 제거하고 Purge Job/receipt용 최소 opaque tombstone만 receipt expiry까지 유지 |
| Operational | Dispatch/Summary Job, Provider Attempt, Context Lease, idempotency/replay record에서 content와 민감 reference를 제거하고 bounded state/timestamp/safe reason만 제한 보존 후 삭제 |
| Audit/usage | 소유 도메인 retention을 따르며 raw content/token/hash/prompt/private source 미포함 |
| Purge control | Purge Job, verifier-hash receipt와 delete 응답 유실 복구용 encrypted receipt replay를 각각 정해진 TTL/receipt expiry까지만 유지 |

Legal hold는 runtime/session 접근을 되살리지 않는다. 보존이 강제된 content는 runtime query와 provider context에서 분리된 compliance boundary에 격리하고 public status에는 hold의 내부 사유를 노출하지 않는다. Purge receipt는 content access가 아니라 job status만 허용한다.

Public purge는 발급 후 7일 안에 completed/completed_with_hold/terminal_failure 중 하나로 닫는다. Retryable failure가 7일을 넘으면 dead-letter와 운영 alert를 남기고 terminal failure로 승격한다. Receipt는 terminal 후 최소 24시간, 발급 후 최대 8일까지 유효하다. V1의 발급 시점 고정 receipt expiry는 정확히 8일이며 더 짧은 configuration을 허용하지 않는다. Legal hold는 compliance 격리가 durable해진 시점에 completed_with_hold로 terminal 처리하며 hold 해제까지 public job을 running으로 유지하지 않는다. `completed_with_hold`와 `terminal_failure`에는 `memory.session.purged`를 만들지 않는다. Hold 해제 후 별도 compliance erasure process가 실제 삭제를 완료한 시점에만 physical purge complete를 기록하고 public terminal status/receipt는 재개하지 않는다.

`completed`는 configured content-bearing live store/cache, conversation access-token replay와 backup/export retention contract가 삭제 또는 승인된 irreversible crypto-erasure marker를 모두 반환한 경우에만 허용한다. 위 표의 최소 purge-control tombstone, receipt verifier와 encrypted delete-response replay만 정해진 TTL/receipt expiry까지 예외로 남길 수 있으며 raw session content 접근에는 사용할 수 없다. Unknown/partial marker는 낙관적으로 완료 처리하지 않는다.

MBA-317은 `conversation_idempotency_records.retention_expires_at`과 `conversation_secret_replays.expires_at` 기준의 Memory-owned periodic retention task를 제공한다. Gateway와 Log System runtime image는 이 업무 모듈을 import할 수 있도록 `apps/memory`를 포함한다. Helm의 기본 활성 singleton Beat는 shared Celery schedule을 발행하고 Log worker queue는 task 실행 host로 소비할 뿐, 정책 소유자는 Memory다. Task는 1분마다 만료 idempotency parent와 더 짧은 TTL의 replay child에 각각 최대 500개의 독립된 logical candidate quota를 보장한다. 같은 transaction에서 parent를 `FOR UPDATE SKIP LOCKED`로 삭제해 FK cascade로 종속 replay와 uniqueness claim을 함께 제거한 뒤, parent backlog와 무관하게 replay child도 별도 `FOR UPDATE SKIP LOCKED` quota로 삭제한다. 이는 database backup의 즉시 crypto-erasure를 증명하지 않는다. Session/Turn/Summary를 지우고 purge terminal marker를 만드는 physical purge worker는 여전히 MBA-320 범위이므로 표준 배포의 `MEMORY_PUBLIC_PURGE_WORKER_READY`는 false다. 승인된 external crypto-erasure/database-backup 미사용 mode와 실제 worker readiness가 모두 확인되기 전에는 public lifecycle activation을 거부한다.

## Application Use Cases

### ResolveOrCreateSession

- canonical runtime identity 검증
- authenticated/public audience binding
- deployment ID/version 또는 snapshot hash와 conversation mapping/Memory policy version 고정
- Access Grant 검증·발급
- retention policy 적용
- contract/storage generation 고정

기존 session과 요청 deployment binding이 다르면 active deployment를 따라 자동 rebind하지 않는다. Public은 resource-hiding 후 새 conversation 흐름, authenticated internal surface는 typed version conflict와 명시적 new-session 흐름을 사용한다.

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

- Create/new
- Close
- Reset
- Delete/tombstone
- Retention expiry
- Durable purge
- Access Grant replacement/revoke
- Source invalidation/revalidation

Public close는 session lifecycle에서 current grant의 사용 범위를 transcript-only로 제한하되 새 grant issue audit을 만들지 않는다. Transcript-only grant는 run/reset에는 사용할 수 없지만 privacy delete에는 사용할 수 있다. Reset은 old grant를 즉시 revoke하고 새 session/grant를 원자 발급하며, delete는 active 또는 transcript-only old grant를 즉시 revoke한다. 이는 grace rotation이 아니며 delete status는 별도 purge receipt가 소유한다.

Close/reset/delete는 짧은 non-locking DB preflight에서 token verifier, immutable deployment/grant/session scope와 exact existing idempotency를 확인하고 transaction을 해제한다. Transcript와 public deployment lookup 같은 read-only 경로도 App row를 잠그지 않는다. 새 logical request만 Redis admission을 수행하고, 같은 operation/scope/idempotency key/fingerprint의 concurrent retry는 HMAC request marker로 budget을 한 번만 소비한다. Admission 완료 뒤 새 server time을 취득한 mutation transaction은 App row를 명시적으로 잠그고 current binding, grant state, session expiry와 idempotency reservation을 다시 검증하므로 대기 중 발생한 revoke/expiry/redeploy race를 fail-closed한다. 일치하는 completed delete record는 Grant/Session 물리 삭제 뒤에도 stable App과 versioned access-token verifier에 결합된 bounded authorization tombstone으로 식별하며, 다른 token·App·fingerprint에는 resource-hiding으로 실패한다. Secret exact replay는 필요한 purge/replay row lock을 모두 획득한 뒤 중앙 replay helper가 fresh clock으로 idempotency parent와 encrypted child의 expiry를 다시 검증한다. 따라서 request-start timestamp는 잠금 대기로 이미 지난 replay window를 되살리지 못한다. Bounded response replay는 최초 성공 시점의 lifecycle/revision, contract/expiry와 필요한 previous lifecycle/revision만 typed nullable column으로 저장한 content-free snapshot을 사용한다. Mutable Session/Purge 상태를 response로 다시 투영하지 않고 raw response와 capability도 snapshot에 포함하지 않으며 legacy/null/corrupt snapshot은 현재 상태로 추정하지 않고 fail-closed한다. Mutation 실패 시 pending record와 state 변경을 rollback한다.

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

Public grant/purge receipt 응답 유실 복구만 담당하는 bounded port다.

- Idempotency record ID, organization, operation, stable App scope, idempotency-key identity, request fingerprint와 replay purpose를 associated data로 사용하는 application-level envelope encryption
- 승인된 key-management capability와 key version
- Access Grant token 최대 10분, Purge receipt 최대 24시간의 bounded TTL, read-on-replay와 irreversible delete
- Stored associated-data digest가 현재 immutable idempotency identity에서 재계산한 digest와 같고 같은 scope/key/fingerprint인 경우에만 decrypt 가능
- 일반 mutation idempotency record는 secret replay lifetime을 포괄하되 최대 24시간으로 제한하고 Purge Job/receipt의 최대 8일 lifecycle과 분리
- 최초 성공 응답의 content-free typed lifecycle snapshot은 idempotency parent에 저장하고 raw response/capability는 별도 encrypted replay 외에는 저장하지 않음
- Delete replay authorization tombstone은 organization, stable App ID, operation, idempotency identity와 versioned access-token HMAC verifier를 all-or-none으로 저장하고 raw token, session content 또는 private source를 저장하지 않음

Access Grant/Purge Job table에는 verifier hash만 두고 ciphertext를 섞지 않는다. Purge Job은 stable App ID와 발급 시점 deployment ID/version·audience snapshot을 함께 보존한다. Memory application은 encryption algorithm이나 raw key를 직접 선택하지 않으며 replay store unavailable이면 새 secret을 중복 발급하지 않고 fail-closed 한다.

Access Grant token replay TTL이 끝난 same-key request는 `memory.secret_replay_expired`로 닫는다. Replay store 조회와 필요한 purge/replay row lock 뒤의 fresh clock이 idempotency parent retention, replay parent TTL과 child expiry를 판정하며 request-start timestamp는 TTL을 연장하지 않는다. Replay store가 만료 secret을 대신해 새 grant/receipt를 발급하거나 rotation하지 않는다.

Live database의 만료 idempotency parent와 replay child는 Memory-owned periodic retention use case가 각각 독립된 bounded batch quota를 보장하고 하나의 transaction으로 삭제한다. Parent 삭제는 종속 replay를 cascade하고 scope/key uniqueness claim을 해제하며, parent backlog가 replay child 정리를 굶기지 못한다. Backup에서의 복구 불가능성은 replay store가 임의로 추정하지 않고 deployment activation 시 승인된 external crypto-erasure/no-backup contract로 확인한다.

### Source Authorization

Source kind별 domain capability를 호출한다.

- Organization membership/subject state
- Knowledge `use`, source ACL와 lifecycle
- Connector/tool resource access
- Subworkflow/app/deployment scope
- System policy/version validity

Memory가 permission row, connector ACL 또는 deployment audience 규칙을 직접 조합하지 않는다.

Bulk authorization result는 dependency별 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 반환한다. Source ACL이 있는 resource는 ACL revision을 decision revision에 반영한다. Public audience는 `anonymous_public_audience` principal kind를 사용하며 subject revision을 합성하지 않는다. Required field가 없거나 adapter timeout/unknown이면 fail-closed 한다. Memory가 source domain의 revision을 자체 합성하지 않는다.

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

Raw Memory content/token/source를 제외한 lifecycle, decision, status와 safe reason을 기록한다. Security/lifecycle mutation은 audit 또는 durable outbox 기록 실패 후 성공으로 처리하지 않는다. Authenticated request actor는 실제 요청 사용자이고 public request actor는 `actor_id=null`, `actor_type='public'`이다. 비동기 physical purge/compliance completion은 `actor_id=null`, `actor_type='system'`으로 기록한다. Access Grant, execution/credential/billing principal과 app/deployment owner를 audit actor로 대체하지 않는다.

AuditLog canonical action은 `memory.session.created/closed/reset/delete_requested/purged`, `memory.grant.issued/revoked`를 사용한다. ADR-0008의 `memory.grant.rotated` 명칭은 향후 standalone rotation 계약용으로 예약되며 V1은 발행하지 않는다. Public create는 session created + grant issued, close는 session closed만 기록한다. Reset은 old session reset/grant revoke와 new session created/public grant issued를 각각 한 번 기록하고 closed를 중복 생성하지 않는다. Delete request는 session delete_requested와 active public grant revoke를 기록한다. Physical erasure 완료만 session purged를 기록하며 completed_with_hold/terminal_failure에는 금지한다. Organization-scoped row는 safe `organization_id`를 필수 metadata로 포함한다. 정상 turn/summary 상태는 high-cardinality operational event/trace/metric으로 기록하고 AuditLog row를 만들지 않는다. Permission/policy 차단은 `permission.denied`/`policy.block`, provider 호출은 `llm.call`, workflow 실행은 `workflow.execute`를 재사용한다.

### Runtime Worker Composition And Activation

Gateway publisher와 Worker는 exact task name `workflow.execute_conversation_turn`을 공유한다. Worker process는 이 task를 시작 시 import/register하고, production handler는 reference-only envelope을 검증한 뒤 실제 DB-backed Memory/Workflow admission, frozen deployment graph, ProviderExecutionCapability/usage adapter를 조립한다. 이름만 등록된 placeholder 또는 test fake는 worker readiness 근거가 아니다. 각 실제 delivery는 서로 다른 lease owner를 사용하고 active lease의 다른 owner는 mutation 전에 fence한다. Task 예외는 safe code만 가진 최대 3회의 bounded recovery delivery로 처리하고 첫 countdown은 execution lease보다 길게 둔다. Stable execution/provider attempt와 assistant checkpoint가 provider 재호출을 막는다.

V1 provider request timeout 상한은 180초이고 Workflow execution lease는 210초,
첫 bounded recovery는 211초 뒤 시작한다. Typed permanent provider preparation 실패는
provider 미전송 상태로 terminal 처리하지만 transient/untyped preparation failure는 retry
가능하게 남긴다. Provider 응답을 받은 owner도 checkpoint와 usage success 전에 current
lease generation을 다시 검증한다.

Public execution observability는 `conversation_workflow_execution_events`의 content-free
durable journal에 public actor와 safe opaque correlation/event/reason만 저장한다. Raw
input/output, prompt, context, token과 provider response column은 두지 않는다. Journal
write 실패는 task retry 대상으로 남기며 admission/event unique key와 terminal redelivery가
누락 event를 idempotent하게 복구한다.

Context attempt, Memory Turn/checkpoint, Workflow admission과 journal은 별도 transaction이므로
각 commit 직후 crash를 reference-only recovery state로 다룬다. Active resolve보다 먼저
deterministic admission/execution/attempt와 frozen deployment correlation만 조회하되, runtime이
여전히 usable한 최초 published delivery는 정상 경로로 통과시킨다. Close, grant revoke 또는
active deployment 교체로 runtime이 stale이면 새 owner가 admission lease generation을
획득한 뒤에만 dispatch/Turn/entry/session과 admission/journal을 terminal로 수렴시킨다.
이 cleanup은 raw content를 읽거나 provider 권한을 복원하지 않는다. Running attempt는
ADR-0069 usage ledger를 권위로 intent/provider-started/terminal을 분류하며, lifecycle 변경
전에 저장된 provisional assistant checkpoint는 actual usage 사실을 보존하되 approved
content로 승격하지 않고 reject한다.

Main-generation capability의 server-owned 상한은 Worker 환경의 다음 세 positive integer에서만 읽는다.

- `MEMORY_RUNTIME_PROVIDER_INPUT_TOKEN_CAP`
- `MEMORY_RUNTIME_PROVIDER_OUTPUT_TOKEN_CAP`
- `MEMORY_RUNTIME_PROVIDER_COST_CAP_MICROUSD`

이 값에는 임의 기본값을 두지 않는다. 누락, boolean, 0 이하 또는 정수 형식 오류는 composition 단계에서 provider I/O 전에 fail-closed한다. Client/Access Grant/graph payload는 이 상한을 설정하거나 늘릴 수 없다. 표준 배포는 runtime과 worker-ready를 default-off로 유지하며, 운영자가 승인한 상한, versioned queue/capability worker routing, schema와 dependent readiness를 함께 확인하기 전 `MEMORY_PUBLIC_RUNTIME_WORKER_READY=true`로 전환하지 않는다.

Gateway process는 route serving 전에 public runtime 설정 검증을 실행한다. Runtime이
활성화됐는데 lifecycle, worker readiness, content encryption/fingerprint key 또는 key
분리가 불완전하면 첫 요청까지 오류를 늦추지 않고 startup을 fail-closed한다.

## Runtime Sequence

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
       -> commits usage intent
       -> revalidates current credential permission/relation/egress/capability binding
       -> commits Memory context-attempt marker
       -> commits canonical usage provider_started
       -> invokes provider
  -> LLM node consumes untrusted Memory Context
  -> Workflow propagates RuntimeDataDependencyEnvelope
  -> optional explicit node projection
  -> Workflow maps final assistant output
  -> Memory stores deterministic provisional assistant checkpoint
  -> Provider usage records terminal success
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

- New conversation
- Continue current conversation
- Close/reset/delete
- Retention and public/internal scope indicator
- Degraded Memory status
- Conflict/retry state

Public/internal Chatbot은 visual component를 재사용할 수 있지만 backend runtime surface, API/auth adapter, CORS/Origin, deployment access policy와 session namespace를 분리한다. Authenticated internal surface는 별도 기능이 구현된 뒤에만 제공한다. Public UI가 authenticated history나 private source 상태를 추론하게 해서는 안 된다.

Authenticated adapter는 bootstrap에서 받은 session-bound CSRF token을 `X-CSRF-Token`으로 전송하고 public adapter는 authentication cookie나 CSRF token을 conversation credential로 사용하지 않는다. Public grant는 탭 단위 sessionStorage에만 보관한다.

Public delete 성공 시 Client는 conversation grant를 즉시 제거하고 별도 purge receipt만 탭 단위 sessionStorage에 보관한다. Purge terminal/expiry 뒤 receipt도 제거하며 grant와 receipt를 같은 header/storage key로 혼용하지 않는다.

### Abuse Protection Adapter

Public session create/run 전에 distributed atomic rate/concurrency limiter를 호출한다. Trusted proxy configuration으로 canonical network source를 만들고 grant/deployment/network/organization scope를 함께 평가한다. Limiter unavailable은 public create/run을 fail-closed하며 client-provided forwarding header를 그대로 신뢰하지 않는다.

### Transcript

Server `ConversationTranscriptView`를 사용하고 client React state를 source of truth로 간주하지 않는다. Refresh/reset/delete 후 server lifecycle과 visible messages가 일치해야 한다. Transcript에 보이는 turn과 model Memory Context가 다를 수 있음을 safe 상태로 표현한다.

## Observability

허용:

- session/turn/entry safe opaque reference
- public/authenticated audience
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
- Legacy flag request는 실행 compatibility를 위해 정규화할 수 있지만 log-derived history는 기본 context에서 제외한다.
- Deployment preflight는 Worker capability와 contract/storage generation을 검증한다.
- Same session은 하나의 reader/writer generation만 사용한다.
- Same session은 생성 시 deployment version/snapshot과 conversation mapping/Memory policy version에 고정하며 active deployment 변경에 자동 rebind하지 않는다.
- Target task는 versioned queue 또는 capability 전용 worker pool에서만 소비하고 개별 Worker가 envelope을 재검증한다.
- Global canvas toggle, Chatbot all-node forced ON과 reserved business input는 migration gate 이후 제거한다.
