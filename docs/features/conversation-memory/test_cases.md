# Conversation Memory Test Cases

Status: Draft

## Test Principles

- 숫자 coverage보다 tenant isolation, current authorization, idempotency, read-your-writes와 cost consistency를 우선한다.
- Domain/application test는 DB, FastAPI, Celery와 provider 없이 실행 가능해야 한다.
- Adapter contract는 실제 PostgreSQL transaction, constraint와 query behavior를 검증한다.
- 초기 public Chatbot, 후속 authenticated internal Chatbot, session 미지원 Workflow Editor test와 non-conversational trigger를 분리한다.
- Execution subject, credential/billing principal, audit actor와 public Access Grant를 서로 대체하지 않는지 검증한다.
- Secret, raw private source, raw Memory content와 exact denied count를 assertion failure/output에 남기지 않는다.
- At-least-once delivery, out-of-order event, partial failure와 late completion을 정상 운영 조건으로 검증한다.

## Architecture Boundary Tests

- MEM-TC-ARCH-001: `apps/memory/domain`은 FastAPI, Celery, SQLAlchemy와 provider SDK를 import하지 않는다.
- MEM-TC-ARCH-002: `apps/memory/application`은 Gateway/Workflow concrete module과 ORM model을 import하지 않는다.
- MEM-TC-ARCH-003: Gateway는 outer composition root에서만 Gateway-specific Memory dependency를 조립한다.
- MEM-TC-ARCH-004: Workflow Engine은 outer composition root에서만 runtime-specific Memory dependency를 조립한다.
- MEM-TC-ARCH-005: Workflow Worker가 Gateway concrete permission/service를 import하지 않는다.
- MEM-TC-ARCH-006: Memory persistence adapter 외 신규 production code가 Memory ORM model을 직접 query/mutate하지 않는다.
- MEM-TC-ARCH-007: Gateway/Workflow composition이 같은 Memory domain/application contract suite를 통과한다.
- MEM-TC-ARCH-008: Memory package import가 DB connection, Celery registration 또는 provider client 생성 side effect를 만들지 않는다.

## Domain Policy Unit Tests

### Session Aggregate And Turn State

- MEM-TC-DOM-001: Active session에서 expected lifecycle revision이 일치하고 active turn이 없을 때만 turn을 시작한다.
- MEM-TC-DOM-002: 같은 canonical idempotency key와 같은 fingerprint retry는 기존 logical turn을 반환한다.
- MEM-TC-DOM-003: 같은 canonical idempotency key와 다른 fingerprint는 duplicate request conflict다.
- MEM-TC-DOM-004: Fingerprint는 raw prompt/secret을 저장하지 않고 canonical payload 차이를 식별한다.
- MEM-TC-DOM-005: Delivery attempt ID가 달라도 logical turn은 중복 생성되지 않는다.
- MEM-TC-DOM-006: Pending에서 completed/failed/cancelled 전이만 허용한다.
- MEM-TC-DOM-007: Terminal turn의 다른 terminal state 재전이를 거부한다.
- MEM-TC-DOM-008: Clock expiry, close/reset/delete 이후 start/complete를 거부한다. Expiry worker가 lifecycle을 아직 전이하지 않았어도 command의 `now`가 idle/absolute boundary에 도달하면 late write를 fail-closed 한다.
- MEM-TC-DOM-009: Stale lifecycle revision 또는 turn version write를 거부한다.
- MEM-TC-DOM-010: Late completion이 delete-pending/deleted session을 되살리지 않는다.
- MEM-TC-DOM-011: 서로 다른 organization에서 같은 session ID를 사용해도 safe hidden/fail-closed 한다.
- MEM-TC-DOM-012: Sequence와 lifecycle/content/turn version boundary를 검증한다.
- MEM-TC-DOM-013: Summary/projection content revision 변경은 pending turn completion을 stale로 만들지 않는다.
- MEM-TC-DOM-014: Failed/cancelled turn의 user/assistant entry는 기본 Memory Context에서 제외된다.
- MEM-TC-DOM-015: Turn의 pending_dispatch/queued/running/terminal 전이와 dispatch/attempt generation을 검증한다.
- MEM-TC-DOM-016: Dispatch/claim deadline 만료는 active turn을 영구 점유하지 않고 retryable 또는 terminal recovery state로 전이한다.

### Node Memory Configuration

- MEM-TC-DOM-020: Config 없음과 `enabled=false`는 Memory repository/summarizer를 호출하지 않는다.
- MEM-TC-DOM-021: Turn/token bound의 0, 음수, overflow와 server upper bound 초과를 거부한다.
- MEM-TC-DOM-022: Unknown strategy/source/write/failure policy를 fail-closed 한다.
- MEM-TC-DOM-023: Selected node가 graph에 없거나 다른 workflow node이면 거부한다.
- MEM-TC-DOM-024: Channel/node identifier 길이와 문자 allowlist를 검증한다.
- MEM-TC-DOM-025: User-global 또는 arbitrary SQL/filter/provider option을 거부한다.
- MEM-TC-DOM-026: Node Memory OFF인 node는 context lease와 summary usage를 만들지 않는다.

### Candidate And Summary Policy

- MEM-TC-DOM-030: 같은 organization/session/channel의 completed entry만 sequence 순으로 선택한다.
- MEM-TC-DOM-031: Expired/closed/invalidated entry를 제외한다.
- MEM-TC-DOM-032: Max turn/token budget를 넘지 않는다.
- MEM-TC-DOM-033: Summary source revision 또는 policy/summarizer version이 다르면 stale 처리한다.
- MEM-TC-DOM-034: 같은 channel의 명시적 node projection만 선택한다.
- MEM-TC-DOM-035: Summary가 source entry dependency 합집합을 상속한다.
- MEM-TC-DOM-036: Source entry 하나가 무효화되면 관련 summary도 stale 처리한다.
- MEM-TC-DOM-037: Provisional node projection은 같은 turn 작업 메모리에서만 보이고 cross-turn candidate에서는 제외된다.
- MEM-TC-DOM-038: CompleteTurn 성공은 허용 projection만 approved로 승격하고 failed/cancelled turn은 모두 rejected/expired 처리한다.

### Authorization And Provenance

- MEM-TC-DOM-040: Public source는 current public eligibility가 있을 때만 anonymous read를 허용한다.
- MEM-TC-DOM-041: Private Knowledge entry는 current subject의 KB `use`와 source ACL을 모두 요구한다.
- MEM-TC-DOM-042: Inactive/suspended/removed membership이면 authenticated private entry를 제외한다.
- MEM-TC-DOM-043: Knowledge/connector/tool/subworkflow가 archived/deleted/revoked되면 derived entry를 제외한다.
- MEM-TC-DOM-044: Content-influencing source 중 하나가 거부되면 entry 전체를 제외한다.
- MEM-TC-DOM-045: Denied source ID/name/path와 exact count를 result에 포함하지 않는다.
- MEM-TC-DOM-046: Authorization unavailable/unknown은 allow가 아니라 fail-closed/degraded policy다.
- MEM-TC-DOM-047: Client/node payload가 canonical dependency ID/capability를 위조해도 무시 또는 거부한다.
- MEM-TC-DOM-048: Provenance cycle, duplicate, unknown source kind와 oversized dependency를 fail-closed 한다.
- MEM-TC-DOM-049: Explicit revoke/regrant는 과거 private-derived entry를 자동 복원하지 않고 current authorization/retention을 다시 평가하며 stale out-of-order invalidation event를 source/version으로 거부한다.
- MEM-TC-DOM-050: Authorization result에 principal kind, authorization decision/resource/policy revision 또는 evaluated_at이 없으면 unknown으로 fail-closed 한다.
- MEM-TC-DOM-051: Anonymous public audience는 subject ID/revision 없이 `anonymous_public_audience` principal kind로 같은 authorization policy를 통과한다.
- MEM-TC-DOM-052: Dependency optional flag를 주입해도 V1 policy는 값·활성 control dependency를 모두 필수로 평가한다.
- MEM-TC-DOM-053: Membership/team/direct permission/public visibility/source ACL/lifecycle 변경은 authorization decision revision을 바꾸고 stale result를 거부한다.
- MEM-TC-DOM-054: 동일한 상수 output이라도 private predicate에 의해 branch가 선택되면 predicate dependency를 보존하며, 선택되지 않은 branch의 값 dependency는 합산하지 않는다.

## Application Use Case Tests

### Start And Complete Turn

- MEM-TC-APP-001: 한 workflow execution에서 user turn을 한 번만 기록한다.
- MEM-TC-APP-002: Multi-LLM workflow에서도 mapped final assistant turn을 한 번만 기록한다.
- MEM-TC-APP-003: Runtime이 첫 input/마지막 LLM output을 임의로 conversational mapping하지 않는다.
- MEM-TC-APP-004: Failed node output은 default intermediate projection으로 저장하지 않는다.
- MEM-TC-APP-005: 명시적 node/channel write만 bounded projection을 저장한다.
- MEM-TC-APP-006: Redaction failure와 private-derived provenance incomplete를 fail-closed 한다.
- MEM-TC-APP-007: Required write가 완료된 후 다음 turn에서 즉시 읽는다.
- MEM-TC-APP-008: Pending user turn 이후 workflow 실패는 failed state로 남지만 다음 default context에서는 제외한다.
- MEM-TC-APP-009: Timeout 후 late success가 lifecycle/content revision을 임의 변경하지 않는다.
- MEM-TC-APP-010: Reset과 Complete 경합에서 하나의 lifecycle 결과만 commit한다.
- MEM-TC-APP-011: Audit/outbox 실패 시 security/lifecycle mutation을 rollback한다.
- MEM-TC-APP-012: StartTurn과 TurnDispatchJob은 같은 UoW에서 함께 commit/rollback된다.
- MEM-TC-APP-013: Commit 직후 Gateway crash 또는 broker publish 실패에도 dispatcher가 job을 재claim한다.
- MEM-TC-APP-013A: Gateway publisher가 claim commit 뒤 broker 전송 오류를 받으면 current owner/generation만 claim을 retry-eligible reconciliation으로 해제한다. 같은 idempotency key의 즉시 retry는 같은 Turn/Dispatch를 재사용해 재발행하고, acknowledged 또는 다른 active owner의 claim은 중복 발행하지 않는다. 최종 attempt 고갈은 dispatch만 terminal로 남기지 않고 Turn failed, provisional user entry rejected, session active turn released를 같은 terminal finalization UoW에 반영하며 중단된 finalization은 exact retry가 복구한다.
- MEM-TC-APP-014: Publish 성공 응답 유실과 duplicate publish는 같은 dispatch/turn을 재사용한다.
- MEM-TC-APP-014A: Durable Workflow admission 전 duplicate publish는 admission에서 제거되고 admission 이후 outcome unknown은 arbitrary execution 재실행 없이 Workflow reconciliation 또는 safe terminal failure로 닫힌다.
- MEM-TC-APP-015: Required CompleteTurn 실패는 success/applied를 반환하지 않고 durable execution result로 CompleteTurn만 재시도한다. 최초 terminal commit 뒤 응답 유실 retry는 outcome, expected predecessor version, assistant entry와 protected content identity가 모두 일치할 때 canonical result를 재생하고, 하나라도 다르면 conflict로 거부한다.
- MEM-TC-APP-015A: Provider response는 canonical usage success보다 먼저 deterministic provisional assistant checkpoint로 저장한다. 그 뒤 crash나 acknowledgement 유실이 발생하면 current execution/attempt, provider context attempt와 usage reference에 binding된 protected checkpoint만 복구해 CompleteTurn을 재시도하며 provider를 다시 호출하지 않는다.
- MEM-TC-APP-016: CompleteTurn의 Turn terminal, Session active-turn/content revision, final entry/projection과 required outbox는 모두 commit되거나 모두 rollback된다.

### Dispatch Processing

- MEM-TC-APP-017: Claim/publish/admission observation/reconciliation은 expected state/version과 fencing generation을 검증한다.
- MEM-TC-APP-017A: Stale dispatch state/version/generation은 `memory.dispatch_state_conflict`로 거부되고 adapter raw error를 노출하지 않는다.
- MEM-TC-APP-017B: Periodic dispatch reconciler는 stable bounded due order로 pending/due reconcile-required, expired claim과 terminal cleanup row를 선택한다. 각 item은 독립 transaction으로 처리하고 stale conflict·adapter 실패가 다른 item 진행을 막지 않으며, retry 상한은 Turn/session 점유를 terminal finalizer로 해제한다.
- MEM-TC-APP-018: Persistence/Celery adapter가 application command 없이 dispatch/turn state를 직접 변경하지 않는다.
- MEM-TC-APP-019: Workflow admission 성공 뒤 Memory acknowledgement가 유실되면 dispatch ID lookup으로 같은 admission을 복구하고 execution을 다시 만들지 않는다.

### BuildMemoryContext

- MEM-TC-APP-020: First turn과 작은 window는 summary provider를 호출하지 않는다.
- MEM-TC-APP-021: Window-only path는 read-only이며 usage/projection write가 없다.
- MEM-TC-APP-022: Threshold 초과 시 generation lease owner만 summary provider를 호출한다.
- MEM-TC-APP-023: Parallel node의 같은 cache key 요청은 provider/usage를 한 번만 만든다.
- MEM-TC-APP-024: Lease owner crash/timeout 후 TTL과 reconciliation policy로 안전하게 재획득한다.
- MEM-TC-APP-025: Provider 호출 동안 DB transaction 또는 row lock을 유지하지 않는다.
- MEM-TC-APP-026: Build는 raw context가 아니라 ContextMaterializationPlan handle과 safe metadata만 반환한다.
- MEM-TC-APP-027: Context lease는 session/execution subject 또는 audience/node invocation/ProviderExecutionCapability에 binding된다.
- MEM-TC-APP-028: Build 후 main provider 호출 직전 permission revoke면 context lease를 거부한다.
- MEM-TC-APP-029: Expired/revoked lease의 raw context를 local cache에서 재사용하지 않는다.
- MEM-TC-APP-029A: Summary provider 호출 직전 permission revoke/authorization decision revision 변경이면 generation lease를 거부하고 raw history를 전송하지 않는다.
- MEM-TC-APP-029B: Provider adapter가 유효 lease를 provider attempt로 claim할 때만 raw context를 획득하며 same-attempt retry 외 재사용/만료/mismatch는 fail-closed 한다.
- MEM-TC-APP-029C: ContextMaterializationPlan은 raw text 없이 ordered reference/version/server-keyed digest만 저장하고 digest를 API/telemetry에 노출하지 않으며 materialized output은 bound를 초과하지 않는다.
- MEM-TC-APP-029D: 같은 provider attempt의 claim retry는 같은 plan/version을 반환하고 다른 attempt의 active claim 탈취를 거부한다.
- MEM-TC-APP-029E: Claim commit 후 `provider_started` 전 crash는 claim expiry와 current authorization 재평가 뒤 새 lease/attempt로 복구한다.
- MEM-TC-APP-029F: `provider_started` 후 crash/timeout은 outcome_unknown으로 남고 provider를 자동 재호출하지 않는다.
- MEM-TC-APP-029G: Permission revoke는 미사용/claimed-not-started plan과 lease를 무효화하지만 started provider attempt의 actual outcome/usage reconciliation을 삭제하지 않는다.
- MEM-TC-APP-029H: 다른 attempt claim/stale version은 `memory.provider_attempt_conflict`, started 이후 불명확 결과는 `memory.provider_outcome_unknown`으로 분리한다.
- MEM-TC-APP-029I: `provider_started` marker commit 실패는 provider 미호출이며 claim expiry/recovery 대상이다.
- MEM-TC-APP-029J: Build는 raw content를 읽지 않고 현재 Turn 이전 completed pair reference만 최신순 bounded snapshot한다. Claim은 newest-first contiguous barrier를 적용해 missing/ineligible/oversize pair와 그보다 오래된 pair를 제외하고 선택 pair를 시간순으로 materialize한다.
- MEM-TC-APP-029K: Candidate 0개는 explicit complete-empty dependency proof로 통과하지만 candidate/provenance/tokenizer가 unknown인 경우 empty로 완화하지 않는다.
- MEM-TC-APP-029L: Memory marker만 commit되고 usage가 exact intent인 같은 attempt는 current owner와 fresh binding으로 usage start까지 한 번 진행할 수 있다. Usage started/outcome-unknown/terminal replay는 provider를 호출하지 않고, Memory marker 또는 usage start commit 실패도 zero-I/O다.
- MEM-TC-APP-029M: Materialized history는 current user message 직전의 untrusted history block으로 한 번 삽입되고 system/developer prompt 또는 durable workflow payload로 승격되지 않는다.
- MEM-TC-APP-029N: Provider capability의 organization/deployment version/node invocation/purpose/model/pricing revision/expiry 중 하나라도 lease와 다르면 raw context와 provider 호출을 모두 거부한다.
- MEM-TC-APP-029O: Lease claim은 실제 materialized entry/summary와 정확히 대응하는 RuntimeDataDependencyEnvelope를 반환하고 excluded/stale source를 envelope에 남기지 않는다.
- MEM-TC-APP-029P: Usage intent commit 뒤 Memory marker 전에 credential permission/relation/egress/capability binding을 다시 검증하며 하나라도 바뀌면 Memory marker, usage start와 provider I/O가 모두 0회다.
- MEM-TC-APP-029Q: 20초 context claim 뒤 Worker가 중단되고 211초 recovery delivery가 도착하면 같은 `claimed` attempt의 generation/deadline/version을 CAS로 증가시키고 current authorization을 재검증한다. Stale owner와 다른 attempt는 raw context 또는 provider 권한을 얻지 못한다.
- MEM-TC-APP-029R: Memory `provider_started` marker 뒤 usage start commit 전에 Worker가 중단되고 claim이 만료되면 generation/deadline을 갱신하지 않은 채 동일 capability revision과 canonical usage reference의 marker callback에만 다시 진입한다. 다른 usage reference와 terminal usage는 provider send 권한을 복원하지 않는다.

### Summary Generation Process

- MEM-TC-APP-030: Generation job은 정의된 state transition만 허용한다.
- MEM-TC-APP-031: Provider 성공 후 source revision CAS 실패는 summary를 저장하지 않고 actual usage를 정산한다.
- MEM-TC-APP-032: Summary commit 후 usage commit 실패는 provider를 재호출하지 않고 reconcile한다.
- MEM-TC-APP-033: Usage/reconciliation terminal 전 projection은 context 후보가 아니다.
- MEM-TC-APP-034: Unknown provider outcome은 lease expiry만으로 자동 replay하지 않는다.
- MEM-TC-APP-035: Reconciliation retry가 summary/usage를 중복 commit하지 않는다.
- MEM-TC-APP-036: Outbox consumer 중복 delivery가 state와 audit을 중복 생성하지 않는다.
- MEM-TC-APP-037: Lease 재획득 후 이전 generation owner가 늦게 완료해도 summary는 commit하지 않으며 실제 provider attempt usage는 reconciliation에서 정확히 한 번 정산한다.
- MEM-TC-APP-038: Budget reservation capability가 없는 composition은 window-only이며 summary provider를 호출하지 않는다.
- MEM-TC-APP-039: `summaryModelPolicy=inherit_node`는 node의 approved capability에서 별도 `memory_summary` capability를 파생하고 direct credential/미정의 `organization_default`를 거부한다.
- MEM-TC-APP-039A: Summary reservation은 capability의 provider/model/pricing revision/purpose/token·cost cap/expiry에 binding되며 mismatch 또는 만료 시 provider를 호출하지 않는다.

### Lifecycle

- MEM-TC-APP-040: New conversation은 새 session을 만들고 기존 session을 변경하지 않는다.
- MEM-TC-APP-041: Close 후 runtime context와 mutation은 거부하지만 retention 기간의 scoped redacted transcript read는 허용한다.
- MEM-TC-APP-042: Reset은 old close + new create를 계약대로 처리한다.
- MEM-TC-APP-043: Delete는 즉시 접근 차단 후 delete-pending을 반환하고 purge를 idempotent하게 완료한다.
- MEM-TC-APP-044: Purge가 Session subject/audience binding, Access Grant verifier/source row, Turn content, final/provisional entry·projection, summary, sensitive dependency, raw/materialized context cache·plan, transcript/result와 conversation access-token replay ciphertext를 남기지 않는다. `completed` 뒤에도 delete response 복구용 encrypted purge receipt replay, receipt verifier와 최소 opaque tombstone만 각각 정해진 TTL/receipt expiry까지 허용하고 raw session content를 조회할 수 없다.
- MEM-TC-APP-045: Legal hold가 있어도 runtime read는 즉시 차단된다.
- MEM-TC-APP-045A: Legal hold content는 runtime/provider query에서 격리되고 public purge status는 내부 hold 사유를 노출하지 않는다.
- MEM-TC-APP-046: Temporary authorization outage는 current read만 차단하고 entry를 영구 invalidation하지 않는다.
- MEM-TC-APP-047: Delete 직후 grant/session 접근은 차단되고 Session row가 물리 삭제되어 Purge Job의 nullable session reference가 사라진 뒤에도 durable organization/stable App/issuance deployment/audience snapshot으로 scoped purge receipt/status를 terminal state까지 조회한다. 같은 App의 재배포 뒤에는 조회가 유지되고 URL slug가 다른 App으로 재할당되면 resource-hiding으로 거부된다.
- MEM-TC-APP-048: Public close는 grant를 transcript-only로 축소하고 run/reset mutation을 거부하되 privacy delete와 same-key close replay는 허용한다. Reset/delete는 old grant를 revoke한다.
- MEM-TC-APP-049: Dispatch/Summary Job, Provider Attempt, Context Lease와 idempotency record는 purge 뒤 content/private reference 없이 bounded opaque state/timestamp/safe reason만 남고 retention 만료 후 삭제된다. Idempotency parent 삭제는 종속 encrypted replay를 cascade하고 scope/key uniqueness claim을 해제한다.
- MEM-TC-APP-050: Audit/usage record는 별도 retention을 유지해도 raw content/token/hash/prompt/private source를 포함하지 않으며 purge job/receipt는 receipt expiry 뒤 삭제된다.
- MEM-TC-APP-050A: Live store/cache/replay 또는 backup/export erasure marker 하나라도 unknown/partial이면 purge를 completed/purged로 기록하지 않고 retry/terminal failure 또는 compliance isolation로 닫는다.
- MEM-TC-APP-051: `completed_with_hold`와 `terminal_failure`는 `memory.session.purged`를 만들지 않고, hold 해제 후 별도 compliance erasure 완료가 한 번만 purged를 만든다. Public terminal status/receipt는 재개하지 않는다.
- MEM-TC-APP-052: Public create/close/reset/delete request는 public actor, asynchronous physical purge/compliance completion은 system actor를 사용하며 action·target·actor·건수가 ADR-0030 matrix와 정확히 일치하고 retry에서 중복되지 않는다.
- MEM-TC-APP-053: Public request/grant audit actor는 `actor_id=null`, `actor_type='public'`이며 app/deployment owner, credential/billing principal 또는 Access Grant reference로 대체되지 않는다.
- MEM-TC-APP-054: Session은 deployment ID/version 또는 snapshot hash와 mapping/Memory policy version에 고정되고 active deployment 변경에도 기존 binding을 유지한다.

## Adapter Contract Tests

### PostgreSQL Repository And UnitOfWork

- MEM-TC-DB-001: Tenant/session/sequence와 request idempotency unique constraint를 검증한다.
- MEM-TC-DB-002: Concurrent StartTurn에서 active-turn/sequence invariant를 보존한다.
- MEM-TC-DB-003: Close/delete와 append/complete race를 검증한다.
- MEM-TC-DB-004: Lifecycle/content/turn/source CAS가 목적에 맞는 version만 비교한다.
- MEM-TC-DB-005: Entry/dependency organization mismatch constraint를 검증한다.
- MEM-TC-DB-006: Source invalidation과 summary stale update가 transaction rollback된다.
- MEM-TC-DB-007: Access Grant token hash uniqueness, `active|transcript_only|revoked|expired` 전이, immediate revoke와 서로 다른 create/reset에서 발급된 grant 격리를 검증한다. Grant의 session/deployment ID·version/audience는 Session canonical binding을 composite FK로 참조하며 불일치 row를 DB에서 거부한다. V1 schema는 rotated-grant chain이나 grace-window 상태를 요구하지 않는다.
- MEM-TC-DB-008: Raw public token column이 존재하지 않는다.
- MEM-TC-DB-009: Memory content encryption key unavailable/rotation contract를 검증한다.
- MEM-TC-DB-010: Content/provenance 최대 크기를 application과 DB 양쪽에서 검증한다.
- MEM-TC-DB-011: Audit/outbox write 실패 시 전체 mutation이 rollback된다.
- MEM-TC-DB-012: Composite index query plan baseline과 bounded result를 검증한다.
- MEM-TC-DB-013: Additive migration upgrade/downgrade가 기존 WorkflowRun/NodeRun을 보존한다.
- MEM-TC-DB-014: Turn/dispatch/audit outbox 중 하나의 insert 실패는 전체 StartTurn을 rollback한다.
- MEM-TC-DB-015: Concurrent dispatcher claim은 하나의 current claim generation만 획득한다.
- MEM-TC-DB-016: Expired owner의 publish/ack와 새 owner claim 경합에서 fencing generation이 stale write를 거부한다.
- MEM-TC-DB-017: Public token replay payload는 application encryption과 TTL을 적용하고 만료 후 raw token을 복구할 수 없다.
- MEM-TC-DB-017A: Replay ciphertext는 idempotency record/organization/operation/purpose와 scope digest, idempotency-key hash, request fingerprint associated data 및 key version에 binding된다. Stored digest 불일치나 각 immutable binding field 변조, 다른 tenant/key replay, key unavailable과 decrypt failure는 raw fallback 없이 fail-closed 한다.
- MEM-TC-DB-017B: Completed public idempotency record는 lifecycle/revision, contract/expiry와 optional previous lifecycle/revision의 typed safe column만 저장하고 raw response/access token/purge receipt column을 두지 않는다. Nullable legacy snapshot과 불완전·invalid snapshot은 mutable resource fallback 없이 fail-closed한다.
- MEM-TC-DB-018: Reset의 old close/new session/grant 중 하나가 실패하면 전체 lifecycle UoW가 rollback된다.
- MEM-TC-DB-019: Delete의 tombstone/grant revoke/purge job/outbox 중 하나가 실패하면 접근 차단 상태가 부분 commit되지 않는다.
- MEM-TC-DB-020: Purge batch retry/cursor/claim fencing이 같은 entry를 재노출하거나 다른 session을 삭제하지 않는다.
- MEM-TC-DB-021: CompleteTurn 중 Turn, Session, entry/projection, outbox 어느 write/flush가 실패해도 전체 UoW가 rollback되고 active turn이 부분 해제되지 않는다.
- MEM-TC-DB-022: Provider attempt claim/start/outcome transition은 version CAS를 적용하고 same-attempt retry 외 중복 claim을 거부한다.
- MEM-TC-DB-023: Session persistence unique/scope constraint가 deployment ID/version or snapshot hash와 mapping/Memory policy version을 함께 보존한다.
- MEM-TC-DB-024: Purge가 provisional projection과 모든 replay ciphertext를 지우고 operational row의 content-bearing column/reference를 null/erased state로 전이한다.
- MEM-TC-DB-025: Purge Job/receipt는 organization, stable App ID, issuance deployment ID/version, audience snapshot, verifier hash와 terminal status만 receipt expiry까지 유지하고 raw receipt/content를 저장하지 않는다. Receipt expiry는 발급 시점 이후 최대 8일을 넘지 않는다.
- MEM-TC-DB-025A: Delete idempotency authorization tombstone은 organization/stable App/operation/idempotency identity와 versioned access-token HMAC verifier를 all-or-none으로 저장한다. Grant/Session 물리 삭제 뒤 exact replay는 허용하지만 다른 token·App·fingerprint는 거부하고 raw access token을 schema와 fixture 어디에도 저장하지 않는다.
- MEM-TC-DB-026: Legal-hold compliance storage는 일반 Memory query/provider adapter와 물리·권한 경계가 분리된다.
- MEM-TC-DB-027: Production execution scope adapter는 organization/turn/dispatch로 Session, Turn, Dispatch, Access Grant, App, Workflow와 active Deployment를 하나의 locked tenant-bound query에서 조회하며 누락·불일치 binding을 `None`으로 닫는다.

### Authorization Adapters

- MEM-TC-ADP-001: Organization membership/subject 상태를 canonical scope로 평가한다.
- MEM-TC-ADP-002: Knowledge direct/team/manager와 source ACL two-gate contract를 bulk 평가한다.
- MEM-TC-ADP-003: Anonymous public-only와 source-managed public approval를 모두 적용한다.
- MEM-TC-ADP-004: Connector/tool/subworkflow lifecycle과 current access를 평가한다.
- MEM-TC-ADP-005: Dependency별 N+1 query 없이 bounded bulk/fallback을 사용한다.
- MEM-TC-ADP-006: Raw ACL fact와 denied identity를 Memory result에 노출하지 않는다.
- MEM-TC-ADP-007: Source adapter는 decision/principal kind/authorization decision revision/resource revision/policy revision/evaluated_at을 반환한다.
- MEM-TC-ADP-008: Source adapter timeout, malformed revision 또는 partial bulk response는 누락 dependency를 allow하지 않는다.
- MEM-TC-ADP-009: Public authorization은 synthetic subject revision 없이 anonymous audience revision contract를 반환한다.
- MEM-TC-ADP-010: Source ACL을 가진 resource는 ACL 변경을 authorization decision revision에 반영하고 old revision lease를 거부한다.

### Privacy Adapter

- MEM-TC-ADP-020: Shared redaction capability의 classification/redacted projection/policy version만 저장한다.
- MEM-TC-ADP-021: Detector unavailable 또는 unsupported sensitive content는 raw fallback 없이 fail-closed 한다.
- MEM-TC-ADP-022: Memory domain이 source-specific PII/secret pattern을 자체 복제하지 않는 import/contract boundary를 검증한다.

### Summarizer, Credential And Budget

- MEM-TC-ADP-030: Approved ProviderExecutionCapability만 provider adapter에 전달한다.
- MEM-TC-ADP-031: Adapter가 임의 model fallback, 조직 기본 preset 또는 owner credential을 선택하지 않는다.
- MEM-TC-ADP-032: Sensitive/data-residency policy가 provider 호출 전에 적용되고 capability egress revision과 일치한다.
- MEM-TC-ADP-033: Capability의 max input/output token, cost cap, expiry와 timeout/retry bound를 적용한다.
- MEM-TC-ADP-034: Provider raw error와 raw response를 durable job/audit에 저장하지 않는다.
- MEM-TC-ADP-035: Capability-bound 예상 비용을 provider 호출 전에 reserve한다.
- MEM-TC-ADP-036: 동시 요청이 같은 budget을 중복 사용하지 않는다.
- MEM-TC-ADP-037: Success는 actual usage commit, 명확한 미호출은 release한다.
- MEM-TC-ADP-038: Timeout/unknown outcome은 무조건 refund하지 않고 reconciliation 상태를 유지한다.
- MEM-TC-ADP-039: 같은 usage idempotency key/capability retry는 중복 과금하지 않는다.
- MEM-TC-ADP-039A: Summary price unavailable/invalid/unknown-zero는 `budget.price_unavailable`이며 reservation/provider/usage를 만들지 않는다.
- MEM-TC-ADP-039B: Approved explicit free model과 unknown-price zero를 구분한다.

## Gateway And API Tests

MBA-317의 자동 검증은 public capability domain/application, encrypted replay/admission adapter, Gateway lifecycle transport와 same-origin CORS boundary를 대상으로 한다. MBA-318은 public StartTurn/dispatch, Workflow admission/execution, bounded context와 provider fence를 추가로 검증한다. Physical purge worker 사례는 MBA-320의 runtime/worker test로 남긴다.

- MEM-TC-API-001: Runtime metadata는 business `inputs`와 분리된다.
- MEM-TC-API-002: Client subject/organization/internal session/storage generation spoofing을 무시한다.
- MEM-TC-API-003: Authenticated endpoint의 invalid/expired auth는 anonymous fallback 없이 401이다.
- MEM-TC-API-004: Organization/deployment/session scope mismatch는 safe hiding policy를 적용한다.
- MEM-TC-API-005: Public Access Grant는 hash/session/deployment ID·version/audience/expiry/state를 검증하고 wrong scope, revoke와 expiry를 즉시 차단한다. Active+previous 최대 두 verifier key 중 stored version과 일치하는 key로 기존 Access Grant와 purge receipt를 검증하되 새 값은 active key로만 발급한다.
- MEM-TC-API-006: Public token의 cross-slug/deployment/audience replay를 거부한다.
- MEM-TC-API-007: Public endpoint의 optional auth header가 private Memory 권한을 높이지 않는다.
- MEM-TC-API-008: Public session은 login 후 authenticated session으로 자동 병합되지 않는다.
- MEM-TC-API-009: Logout 후 authenticated session을 public endpoint에서 사용할 수 없다.
- MEM-TC-API-010: Malformed/oversized token, `Idempotency-Key`와 conversation envelope을 거부한다.
- MEM-TC-API-011: Same canonical idempotency key/different fingerprint는 409 typed conflict다.
- MEM-TC-API-011A: Body의 별도 request ID를 canonical key로 사용하지 않고 unknown field policy에 따라 거부한다.
- MEM-TC-API-012: Stale lifecycle/active-turn conflict는 safe 409 contract다.
- MEM-TC-API-013: New/close/reset/delete authorization과 idempotency를 검증한다.
- MEM-TC-API-013A: Close/reset의 `If-Match`와 `Idempotency-Key` 누락·malformed·stale 값을 거부하고 lifecycle revision을 body와 중복 요구하지 않는다. 같은 key/body라도 `If-Match` revision이 다르면 bounded request fingerprint가 달라져 prior response를 replay하지 않고 duplicate-request conflict로 닫힌다.
- MEM-TC-API-013B: Delete의 `If-Match`/`Idempotency-Key` 누락·malformed·stale 값을 거부하고 같은 key retry는 같은 purge request를 반환한다.
- MEM-TC-API-013C: Session response ETag와 lifecycle revision이 일치하고 reset response는 새 session ETag와 old terminal `previous.lifecycle_revision`을 혼동하지 않는다. Public create/reset/close/transcript의 `expires_at`은 grant, idle과 absolute 만료 중 가장 이른 실제 접근 경계이며 completed replay도 최초 성공 snapshot의 같은 값을 반환한다.
- MEM-TC-API-014: Raw token/Memory content/source/provider error가 response, audit와 log에 없다. `access_token`/`purge_receipt` key와 versioned Access Grant/purge receipt가 포함된 자유 텍스트는 redaction policy가 비활성이어도 공통 tracing 경계에서 항상 마스킹된다.
- MEM-TC-API-015: Public token이 URL, Referer, browser history와 access log에 없다.
- MEM-TC-API-016: Schedule/webhook/API secret가 임의 conversation subject/session을 주입하지 못한다.
- MEM-TC-API-017: Transcript pagination/cursor가 tenant/session을 벗어나지 않는다. MBA-317의 빈 projection도 계약된 `conversation{state,lifecycle_revision,content_revision,expires_at}`, `turns`, `next_cursor` shape을 유지하고 legacy/internal `status/entries`를 노출하지 않는다.
- MEM-TC-API-018: Create는 201, pending run/delete는 202, completed run/lifecycle read는 200 계약을 지킨다.
- MEM-TC-API-019: 같은 idempotency key의 pending/completed/failed replay는 새 task/provider 호출 없이 같은 safe 상태/결과를 반환한다. Completed close/reset/delete의 exact replay는 원 grant/session이 temporal expiry를 지난 뒤에도 각 bounded replay/idempotency TTL 안에서 기존 결과만 복구한다. 최초 성공 뒤 Session이 delete-pending/deleted로 진행되어도 close/delete replay의 lifecycle revision과 ETag는 최초 성공 snapshot과 동일하다.
- MEM-TC-API-020: Public create/reset 응답 유실 뒤 같은 key retry는 최초 응답의 동일 grant만 유지하고 bounded replay record에서 같은 raw token을 반환한다. 최초 생성 Session 또는 reset replacement가 이후 close되어도 replay lifecycle/revision/contract/expiry와 reset previous revision은 최초 성공 snapshot과 동일하다. Reset replay는 old grant를 다시 활성화하거나 replacement grant를 추가 발급하지 않는다. Expired scope의 새 key/fingerprint mutation은 replay로 승격하지 않고 resource-hiding하며 admission budget도 소비하지 않는다.
- MEM-TC-API-020A: Public create idempotency key는 최소 128-bit entropy를 요구하고 raw key를 durable log에 남기지 않으며 정상 network 변경 뒤에도 같은 canonical deployment retry를 복구한다. Parent Origin은 idempotency scope를 바꾸지 않는다.
- MEM-TC-API-021: Access Grant replay 10분 TTL 만료 뒤 same-key create/reset은 `409 memory.secret_replay_expired`를 반환하고 새 grant를 만들지 않는다.
- MEM-TC-API-021A: `memory.secret_replay_expired` 뒤 새 idempotency key의 explicit create는 새 session/grant를 만들며 접근 불가능한 기존 session은 idle expiry/retention cleanup 대상이다.
- MEM-TC-API-021B: Public reset/delete의 expired secret conflict는 exact scope/fingerprint/grant-verifier relation에서만 반환하고 wrong key/token/deployment는 동일한 404다. Parent Origin은 grant scope 입력이 아니다.
- MEM-TC-API-022: Public invalid/expired/revoked/wrong-scope grant와 lifecycle probe는 모두 동일한 safe 404 shape다.
- MEM-TC-API-023: Authenticated owner만 closed lifecycle 409를 받고 scope 밖 caller는 404다.
- MEM-TC-API-024: Authenticated mutation은 CSRF token, exact Origin과 Fetch Metadata 중 하나라도 실패하면 side effect 없이 403이다.
- MEM-TC-API-024A: `X-CSRF-Token`은 authenticated session에 binding되고 다른 session/user token replay, URL token과 auth cookie-derived token을 거부한다.
- MEM-TC-API-025: Public Conversation API는 iframe same-origin 호출만 지원하며 `Access-Control-Allow-Origin`·`Access-Control-Allow-Credentials`와 public CORS preflight grant를 반환하지 않는다. 전역 credentialed CORS 설정, wildcard 또는 deployment parent allowlist가 이 route에 누출되지 않는다.
- MEM-TC-API-025A: Same-origin iframe 호출은 정상 동작하고 external direct JavaScript preflight/fetch는 CORS grant를 받지 못한다. Parent Origin, `Referer`, `Host`, query와 client hint는 grant scope·deployment policy·runtime audience를 바꾸지 않는다.
- MEM-TC-API-025B: V1은 standalone grant rotate endpoint를 노출하지 않고 old/new token grace overlap을 허용하지 않는다. Reset 성공과 동시에 old grant는 사용할 수 없다.
- MEM-TC-API-025C: Workflow Editor test 실행은 별도 Conversation Session을 생성하거나 public/authenticated session route를 재사용하지 않는다. 미승인 session surface 값은 fail-closed한다.
- MEM-TC-API-025D: 새 탭/브라우저는 기존 session용 grant를 재발급받지 못하며 새 idempotency key로 별도 conversation을 생성한다.
- MEM-TC-API-026: Token/transcript/status 응답은 no-store/private/Vary header 계약을 지킨다.
- MEM-TC-API-026A: Public conversation page는 no-referrer/CSP를 반환하고 미허용 third-party script/frame origin과 inline token telemetry를 허용하지 않는다.
- MEM-TC-API-027: Grant/deployment/network/organization rate 또는 concurrency limit 초과는 provider/dispatch 전에 429와 해당 operation window의 실제 남은 시간 범위로 제한한 safe Retry-After를 반환한다.
- MEM-TC-API-028: Public token은 최소 128-bit entropy/version prefix를 가지며 verifier hash/constant-time comparison contract를 검증한다.
- MEM-TC-API-029: Public purge receipt는 URL에 노출되지 않고 revoke된 conversation grant와 별도 scope로 status만 조회한다.
- MEM-TC-API-029A: Public delete 응답 유실 뒤 24시간 replay TTL 안의 same-key replay는 Grant/Session 물리 삭제 뒤에도 stable App과 versioned access-token verifier에 결합된 authorization tombstone을 확인해 추가 receipt를 만들지 않고 encrypted response record에서 같은 receipt와 최초 delete-pending lifecycle revision을 복구한다. Wrong-token/App/fingerprint replay는 resource-hiding으로 거부한다.
- MEM-TC-API-029B: Purge receipt는 terminal 후 최소 24시간과 발급 후 최대 8일 경계를 지키며 durable organization/stable App/issuance deployment/audience snapshot으로 만료 전 terminal 상태를 조회할 수 있다. 같은 App의 재배포는 허용하고 slug의 다른 App 재할당은 거부한다. 발급 시 고정 expiry를 쓰는 V1 구성은 lifetime을 정확히 8일로 기본화하고 더 짧거나 긴 값을 활성화 전에 거부한다.
- MEM-TC-API-029C: Purge job은 발급 후 7일 안에 completed/completed_with_hold/terminal_failure로 닫고 남은 retryable failure는 dead-letter/alert와 terminal failure로 승격한다.
- MEM-TC-API-029D: Purge receipt replay ciphertext 만료 뒤 same-key delete는 `memory.secret_replay_expired`이고 새 receipt를 자동 재발급하지 않는다.
- MEM-TC-API-030: Public idle 24시간/absolute 7일 경계 직전은 유효하고 경계 시각부터 resource-hidden 처리한다.
- MEM-TC-API-031: Public completed turn 100개까지 허용하고 101번째는 dispatch/provider 전에 bounded limit으로 거부한다.
- MEM-TC-API-032: Entry 16 KiB는 UTF-8 byte 기준으로 검증해 multi-byte Unicode가 문자 수 우회로 overflow하지 않는다.
- MEM-TC-API-033: Context 4,096 token server upper bound를 client/node config가 늘릴 수 없다.
- MEM-TC-API-034: Public create/run rate window의 마지막 허용 요청과 첫 초과 요청, window rollover와 concurrent burst를 검증한다. 두 tenant의 create는 deployment, organization과 deployment+network bucket을 공유하지 않으며 global network 또는 synthetic grant bucket을 만들지 않는다. 같은 operation/scope/idempotency key/fingerprint의 concurrent retry는 stable HMAC marker로 budget을 한 번만 소비하고 다른 fingerprint는 marker를 공유하지 않는다. Redis I/O 시 DB transaction/row lock이 없고 read-only preflight/deployment/transcript resolver는 App row를 잠그지 않는다. Mutation은 admission 완료 뒤 새 server time을 취득하고 App row를 명시적으로 잠근 transaction에서 current deployment/grant/session state와 expiry를 다시 검증한다.
- MEM-TC-API-034A: Configured trusted proxy chain은 오른쪽부터 canonical client network를 복원하고 untrusted peer의 forged forwarding header는 network rate scope를 바꾸지 못한다. Unknown identity와 limiter unavailable은 public create/run을 fail-closed 한다.
- MEM-TC-API-035: Raw token replay 10분 경계 직전은 동일 secret을 반환하고 경계 시각부터 `memory.secret_replay_expired`다. Memory-owned periodic cleanup은 만료 idempotency parent와 만료 ciphertext child에 각각 독립된 bounded quota를 보장해 하나의 `SKIP LOCKED` transaction에서 삭제한다. Parent가 quota를 모두 채워도 child cleanup은 실행되고, parent cascade 뒤 같은 scope/key가 영구 conflict로 남지 않으며 최대 24시간인 일반 idempotency retention과 최대 8일인 purge receipt lifecycle을 혼동하지 않는다. Backup erasure contract가 확인되지 않은 환경은 public lifecycle activation을 거부한다.
- MEM-TC-API-035A: Public lifecycle 활성화는 capability HMAC, replay encryption과 admission HMAC key의 존재·형식을 각각 확인한다. Capability/replay keyring은 active+previous 최대 두 개이고, keyring 내부 또는 세 용도 전체에서 어느 active/previous material도 재사용하면 secret을 출력하지 않고 fail-closed한다. 새 verifier/ciphertext는 active key version을 저장하며 previous capability와 replay ciphertext는 각각 원래 expiry/TTL 동안 stored version으로만 소비한다.
- MEM-TC-API-035B: Public lifecycle은 physical purge worker readiness가 누락·false·invalid이면 활성화되지 않는다. 표준 Docker/Helm/Kubernetes 기본값은 feature와 readiness를 모두 false로 유지하고 MBA-320 worker가 실제 배포되기 전 readiness를 true로 주장하지 않는다.
- MEM-TC-API-035C: Public lifecycle 활성화는 route serving 전에 실제 DB introspection으로 필요한 Memory table·column capability를 확인한다. 누락 또는 introspection failure는 process startup을 fail-closed하고 특정 Alembic revision 문자열이나 현재 head와의 일치에는 의존하지 않는다.
- MEM-TC-API-036: Public route에 valid login cookie/authorization을 함께 보내도 execution principal은 anonymous public audience이며 private Knowledge/Memory가 허용되지 않는다.
- MEM-TC-API-037: Authenticated internal Chatbot route는 별도 surface/권한/CSRF/CORS/session namespace를 요구하고 public Access Grant로 호출할 수 없다.
- MEM-TC-API-038: 일반 deployment, schedule, webhook과 API batch는 target Conversation Session을 암묵적으로 생성하지 않는다.
- MEM-TC-API-039: Session-pinned deployment version과 요청 version이 다르면 authenticated route는 typed conflict/new-session action을 반환하고 public route는 동일한 safe 404를 반환한다.
- MEM-TC-API-040: Deployment-owned parent allowlist는 `frame-ancestors` CSP에만 사용한다. 정책 부재·disabled·mismatch는 iframe embed를 fail-closed로 막지만 Public Conversation API의 CORS grant로 재해석하거나 client/env fallback으로 완화하지 않는다.

### MBA-317 Review Regressions

- MEM-TC-API-040A: Canonical conversation create와 trailing-slash redirect alias의 preflight 모두 outer same-origin boundary에서 404이며 global `Access-Control-Allow-*` header를 상속하지 않는다.
- MEM-TC-API-040B: Same logical request는 primary quota를 한 번만 소비하되 별도 HMAC per-request retry bucket의 finite limit을 넘지 못한다. Fixed-window key는 정확한 boundary에서 만료되어 이전 window count를 이월하지 않는다.
- MEM-TC-API-040C: Gateway가 요청별 admission adapter를 조립해도 같은 Redis 설정은 process-scoped client/pool 하나를 재사용한다.
- MEM-TC-API-040D: Lifecycle mutation은 App/grant/session row lock을 모두 획득한 뒤 새 server time으로 grant/session expiry를 재검증한다.
- MEM-TC-API-040E: Cleanup이 지연돼도 retention expiry에 도달한 idempotency row는 lookup/authorized replay에서 제외하며, reservation은 같은 scope/key의 만료 claim을 lock 아래 교체한다.
- MEM-TC-API-040F: Retention task는 parent/child별 독립 batch quota를 유지하고 포화된 batch를 finite per-run budget까지 반복하며, budget 소진 시 남은 backlog를 표시한다.
- MEM-TC-API-040G: Public outer transport boundary는 success, explicit error, dependency Content-Type `415`, body validation `422`, router `404`/`405`, redirect alias와 preflight 모두에 `Cache-Control: no-store`와 `Referrer-Policy: no-referrer`를 적용하고 전역 CORS header와 `Vary: Origin`을 제거한다.
- MEM-TC-API-040H: Access Grant와 purge receipt exact replay가 필요한 purge/replay row lock을 기다리는 동안 TTL boundary를 지나면, lock 뒤 fresh server time이 idempotency parent retention과 replay parent/child expiry를 다시 확인해 `memory.secret_replay_expired`로 닫고 request-start timestamp로 replay window를 연장하지 않는다.

### MBA-318 Review Regressions

- MEM-TC-API-040I: Valid Conversation capability의 Turn status는 safe state, sequence, approved display와 bounded failure reason만 반환한다. Missing/malformed/expired/revoked/wrong deployment·session·turn capability는 동일한 resource-hidden 404이며 model projection, raw provider response와 내부 queue/Worker detail은 없다.
- MEM-TC-API-040J: Turn status `ETag`는 authorization으로 확인한 current Session lifecycle revision과 정확히 일치한다.
- MEM-TC-API-040K: Frozen graph/config가 Memory-on인 public deployment는 valid/invalid contract 모두 conversation envelope 누락을 typed `422`로 거부하고 legacy execution을 호출하지 않는다. Raw `enabled`의 literal false/필드 부재만 Memory-OFF로 인정하고 legacy bool parser가 coercion할 수 있는 숫자·문자열과 다른 malformed 값은 fail-closed한다.

- MEM-TC-API-040L: Request fingerprint primary HMAC key가 회전한 뒤에도 stored key version이 retained keyring에 있으면 same-key/same-input retry는 기존 Turn을 replay하고, 다른 input은 duplicate conflict다. Stored key version이 누락되거나 keyring에서 제거되면 admission, 새 Turn과 dispatch write 전에 `memory.adapter_unavailable`로 fail-closed한다.
- MEM-TC-API-040M: Public transcript는 completed Turn의 approved user/assistant display projection만 AAD-bound decrypt하고 model projection을 반환하지 않는다. Failed/cancelled Turn은 content 없이 safe reason만 반환하며, 51개 이상 결과는 50개 page와 organization/session-bound HMAC cursor로 안정적으로 이어진다. Cursor 변조·재인코딩·cross-session 재사용, retained-key rotation, cross-tenant/session entry, ID/type/lifecycle 불일치와 ciphertext AAD 변조를 검증하고 raw fallback 없이 실패한다. Runtime Worker를 비활성화해도 lifecycle content cipher로 기존 transcript를 복호화한다.
- MEM-TC-API-040N: Completed Turn 0개와 99개는 새 logical request를 허용하고 100개는 admission/Turn/dispatch/provider 전에 거부한다. 100개 상태의 exact retry는 기존 Turn을 반환하며, preflight 뒤 다른 요청이 100번째를 완료하는 race도 locked session transaction의 재검사에서 101번째 write를 차단한다.
- MEM-TC-API-040O: Root public-run의 malformed JSON, non-object body와 Conversation preflight는 endpoint body validation 전에 outer boundary로 분류되어 no-store/no-referrer를 받고 CORS/Vary Origin을 제거한다. Conversation transport signal이 없는 Memory-OFF legacy root request는 기존 CORS 성공 계약을 유지한다.
- MEM-TC-API-040P: Frozen Start `max_length`는 canonical runtime binding에 보존되고 ASCII·다중바이트 UTF-8 입력의 정확한 byte 경계는 허용하며 첫 초과 byte는 admission/write 전에 거부한다. Context `4,096`은 허용하고 `4,097`은 contract validation에서 거부한다.
- MEM-TC-API-040Q: completed/failed/cancelled exact run retry는 새 dispatch publish 없이 `200` turn projection을 반환한다. Completed는 approved display만, failed/cancelled는 bounded safe reason만 반환하며 pending/queued/running은 기존 `202 Accepted` shape를 유지한다.
- MEM-TC-API-040R: Public run 기본 rate는 60초 창에서 deployment 120, organization 600, network 60, grant 20이며 composition, Docker와 Helm standard/production value가 같은 값을 명시한다.

## Workflow Runtime Tests

- MEM-TC-RUN-001: Node Memory config가 graph save/load/copy/deployment snapshot round-trip을 보존한다.
- MEM-TC-RUN-002: Draft 변경이 active deployment Memory policy를 바꾸지 않는다.
- MEM-TC-RUN-003: Explicit user input/final output mapping이 없으면 activation/runtime을 fail-closed 한다.
- MEM-TC-RUN-003A: Gateway와 Worker의 공통 validator는 mapped input 변수가 user prompt에만 존재하고 system/assistant prompt에는 없다는 동일 계약을 적용한다.
- MEM-TC-RUN-004: Multi-answer graph에서 runtime이 final output을 추측하지 않는다.
- MEM-TC-RUN-005: Knowledge/tool/subworkflow dependency가 transform/LLM/final answer까지 합집합으로 전파된다.
- MEM-TC-RUN-006: Provenance-incomplete code/custom result의 private/sensitive write를 거부한다.
- MEM-TC-RUN-007: Enabled node만 Memory를 사용하고 channel이 섞이지 않는다.
- MEM-TC-RUN-008: Context와 Knowledge block은 untrusted label/order를 유지한다.
- MEM-TC-RUN-009: Main provider adapter가 호출 직전 context lease를 provider attempt로 claim하고 current authorization을 재검증한 operation에서 raw context를 materialize한다.
- MEM-TC-RUN-010: Duplicate Worker delivery마다 서로 다른 live owner를 사용하고 active lease의 다른 owner를 provider/Memory mutation 전에 fence한다. Stable execution/provider attempt identity는 유지해 Memory-owned turn/entry/summary/usage mutation과 provider I/O를 중복 생성하지 않는다. Arbitrary node/tool side effect idempotency는 Workflow domain contract로 별도 검증한다.
- MEM-TC-RUN-011: Subworkflow는 explicit bounded context/channel만 상속하고 parent table을 직접 조회하지 않는다.
- MEM-TC-RUN-012: Unsupported contract/version Worker가 새 deployment를 실행하지 않는다.
- MEM-TC-RUN-013: Worker는 contract/storage/minimum capability mismatch를 외부 node side effect 전에 거부한다.
- MEM-TC-RUN-014: Dedicated/versioned queue의 old Worker가 target task를 소비하지 못하고 잘못 라우팅된 task도 runtime guard가 거부한다.
- MEM-TC-RUN-014A: Production Worker는 Gateway가 publish하는 exact task name을 등록하고 실제 DB/Memory/provider application adapter로 조립한다. 등록만 된 placeholder나 test-only composition은 readiness 근거가 아니다.
- MEM-TC-RUN-014B: Production composition의 input/output token과 micro-USD provider 상한은 server-owned required Worker 환경값이며 누락·boolean·0 이하·정수 형식 오류를 provider I/O 전에 fail-closed한다. Client, grant와 graph payload는 상한을 설정하거나 늘리지 못한다.
- MEM-TC-RUN-014C: Gateway process는 public runtime activation 설정을 route serving 전에 검증한다. Runtime이 활성인데 lifecycle/worker readiness/content key/fingerprint key 또는 key 분리가 불완전하면 첫 public run 요청까지 오류를 늦추지 않고 startup을 거부한다.
- MEM-TC-RUN-014D: Registered Conversation task는 application 예외를 safe code로 redaction해 최대 3회의 bounded recovery delivery를 예약한다. 첫 countdown은 production execution lease보다 길어 새 owner가 lease 만료 전에 retry budget을 소진하지 않는다. Provider response checkpoint 뒤 retry는 usage/context terminal 상태를 덮어쓰지 않고 checkpoint로만 completion하며 provider I/O를 다시 수행하지 않는다.
- MEM-TC-RUN-014E: Production execution lease는 최대 provider timeout보다 길고 첫 recovery는 lease 뒤에 시작한다. Typed permanent provider preparation failure는 provider 미전송 terminal state로 닫고 transient failure는 retryable하게 유지한다. Provider 응답 뒤 generation이 바뀌면 stale owner는 checkpoint/usage success/Memory completion을 기록하지 못한다.
- MEM-TC-RUN-014F: Memory provider-start marker 뒤 canonical usage start commit이 확인되지 않으면 provider를 호출하거나 Turn/admission을 terminalize하지 않는다. Bounded retry는 exact intent면 같은 attempt의 usage start를 한 번 계속하고 usage가 이미 provider-started/terminal이면 send 권한을 복원하지 않는다.
- MEM-TC-RUN-014G: Context attempt terminal, Memory Turn terminal과 Workflow admission terminal의 각 commit 직후 crash를 재전달한다. 새 owner가 current admission lease generation을 획득하기 전에는 Memory mutation이 0회이고, 획득 뒤에는 exact predecessor/execution/attempt/result/reason만 provider 재호출 없이 한 terminal projection으로 수렴한다.
- MEM-TC-RUN-014H: Publish 뒤 ACK 전 또는 admitted/running commit 뒤 close, grant revoke, active deployment 교체가 발생하면 active execution 권한은 복원하지 않는다. Historical resolver는 usable fresh `published` delivery를 정상 경로로 통과시키고 stale lifecycle만 deterministic admission claim/fence 아래에서 dispatch ACK, Turn/entry/session과 admission safe terminal로 원자·멱등 정리한다.
- MEM-TC-RUN-014I: Historical running cleanup은 canonical usage ledger를 읽어 intent는 provider 미호출 실패, provider-started/outcome-unknown은 outcome unknown, terminal usage는 canonical 결과로 분류한다. Lifecycle 변경 전에 저장된 provisional assistant checkpoint는 actual usage/context 사실을 보존하면서 reject하고 raw output을 승인하거나 provider를 재호출하지 않는다.
- MEM-TC-RUN-014J: Current input unreadable, context unavailable/conflict와 invalid message mapping 같은 결정적 pre-provider 오류는 current admission fence 아래 context attempt(생성된 경우), Memory Turn과 Workflow admission을 동일 safe reason의 failed terminal로 닫고 provider I/O를 수행하지 않는다. Memory adapter outage와 untyped storage 장애는 terminalize하지 않고 bounded retry가 복구할 수 있게 lease state를 보존한다.
- MEM-TC-RUN-014K: Gateway publisher와 Celery route는 `workflow.execute_conversation_turn`을 exact `conversation-memory-v1` queue로 보내고 Docker, dev, Compose와 Helm의 capable Worker만 그 queue를 소비한다. Wildcard `workflow.*` route보다 exact route가 우선하며 runtime activation은 configured queue가 exact versioned queue가 아니면 startup을 거부한다.
- MEM-TC-RUN-014L: Workflow admission은 admitted/leased 동안 retention expiry가 없고 terminal 전이에서 8일 expiry를 한 번 고정하며 exact finish replay가 이를 연장하지 않는다. Deployment ID/version은 immutable snapshot으로 남되 Deployment FK가 삭제를 막지 않는다. Periodic cleanup은 versioned Conversation queue에서 1분마다 최대 500개 expired terminal row만 `SKIP LOCKED`로 삭제하고 naive clock, 0 또는 초과 batch를 거부한다.
- MEM-TC-RUN-015: Workflow `AdmitExecution(dispatch_id)`은 같은 dispatch에 admission을 하나만 만들고 lookup으로 acknowledgement 유실을 복구한다.
- MEM-TC-RUN-016: Memory는 Workflow execution lease/heartbeat를 변경하지 않고 safe state projection만 반영한다.
- MEM-TC-RUN-017: Knowledge, connector/tool, subworkflow, LLM, transform/code와 system policy producer가 RuntimeDataDependencyEnvelope source-owner contract를 지킨다.
- MEM-TC-RUN-018: Transform/code node는 모든 content input dependency 합집합을 보존하며 dependency를 삭제하거나 canonical ID를 새로 발급하지 못한다.
- MEM-TC-RUN-018A: Condition/Switch의 predicate가 private source에 의존하고 선택 branch가 상수를 반환해도 final envelope은 predicate dependency와 선택 route control context를 보존한다.
- MEM-TC-RUN-018B: Condition/Switch는 선택되지 않은 branch 내부의 값 dependency를 final envelope에 합산하지 않는다.
- MEM-TC-RUN-018C: Loop의 iterable, bound, continue와 termination 판단 dependency는 실행된 body output과 loop aggregate/final output에 전파된다.
- MEM-TC-RUN-018D: Predicate/loop control lineage가 missing/unknown이면 private/sensitive Memory write를 `provenance_incomplete`로 거부한다.
- MEM-TC-RUN-018E: Loop가 0회 실행돼 상수 empty aggregate를 반환해도 iterable/bound/termination 판단 dependency는 final envelope에 남는다.
- MEM-TC-RUN-018F: Nested Condition/Switch/Loop는 실행된 경로의 active control dependency만 bounded deduplicate하고 count/size cap 초과를 fail-closed한다.
- MEM-TC-RUN-019: Subworkflow output은 target deployment version과 child envelope 합집합을 parent에 반환한다.
- MEM-TC-RUN-020: Dependency 없는 code/custom private/sensitive output은 final mapping까지 도달해도 Memory write에서 fail-closed 한다.
- MEM-TC-RUN-020A: Server-derived complete empty envelope과 missing/unknown envelope을 구분하고, 후자를 source 없는 결과로 승격하지 않는다.
- MEM-TC-RUN-021: Main/summary provider call은 각각 purpose가 일치하는 ProviderExecutionCapability, context lease와 budget reservation을 사용한다.
- MEM-TC-RUN-022: Active deployment가 바뀐 뒤 queued old-session task는 pinned snapshot만 실행하거나 version mismatch로 side effect 전에 거부하고 새 active graph로 실행하지 않는다.
- MEM-TC-RUN-023: LLM output은 current Memory Context가 상속한 dependency를 새 retrieval/tool dependency와 합산하고 final entry까지 보존한다.
- MEM-TC-RUN-024: Credential revoke 또는 credential permission decision/verified relation/egress revision 변경 뒤 stale ProviderExecutionCapability는 새 context claim, budget reservation, provider attempt admission과 outbound call 전에 거부된다.

## Client Tests

- MEM-TC-UI-001: Canvas global Memory toggle을 제거하고 LLM node detail config를 저장한다.
- MEM-TC-UI-002: Invalid turn/token/source/channel config를 inline validation한다.
- MEM-TC-UI-003: Selected node picker가 graph node delete/type change에 반응한다.
- MEM-TC-UI-004: New/continue/close/reset/delete가 server session lifecycle과 일치한다.
- MEM-TC-UI-005: Refresh 후 TranscriptView와 visible messages가 일치한다.
- MEM-TC-UI-006: Public/internal mode, retention과 degraded status를 safe하게 표시한다.
- MEM-TC-UI-007: Public login과 authenticated logout에서 history가 자동 전환되지 않는다.
- MEM-TC-UI-008: Credential API 실패를 “credential 없음”으로 오표시하지 않는다.
- MEM-TC-UI-009: Conversational primary node config는 사용자 확인 전 자동 활성화하지 않는다.
- MEM-TC-UI-010: Multi-LLM/ambiguous graph를 자동 migration하지 않는다.
- MEM-TC-UI-011: Keyboard와 screen reader로 Memory config와 lifecycle control을 사용할 수 있다.
- MEM-TC-UI-012: Long label/error가 layout을 깨뜨리거나 private source를 노출하지 않는다.
- MEM-TC-UI-013: Public delete 직후 conversation grant를 제거하고 purge receipt만 별도 sessionStorage key에 유지하며 terminal/expiry 뒤 삭제한다.

## End-To-End Matrix

| ID | Scenario | Expected |
| --- | --- | --- |
| MEM-TC-E2E-001 | Single LLM chatbot, three turns | Previous completed turn context를 정확히 이어감 |
| MEM-TC-E2E-002 | New conversation | Past context 없음 |
| MEM-TC-E2E-003 | Two public browsers create separate conversations | Access Grant/session 격리, 기존 session grant 재발급 없음 |
| MEM-TC-E2E-004 | Two authenticated users | Subject/session 격리 |
| MEM-TC-E2E-005 | Same user, two organizations | Organization 격리 |
| MEM-TC-E2E-006 | Same workflow, two explicit sessions | 업무 context 격리 |
| MEM-TC-E2E-007 | Private Knowledge permission granted | Current authorized memory/evidence 사용 |
| MEM-TC-E2E-008 | Permission revoked before next turn | Private-derived entry/summary/context lease 제외 |
| MEM-TC-E2E-009 | Knowledge/connector archived or deleted | Related entry/summary 제외 |
| MEM-TC-E2E-010 | Log worker delayed/down | Dedicated Memory read-your-writes 유지 |
| MEM-TC-E2E-011 | Summary provider timeout | Configured failure/reconciliation 적용 |
| MEM-TC-E2E-012 | Budget exhausted | Provider 미호출, 비용 계약 유지 |
| MEM-TC-E2E-013 | Multi-LLM workflow | Enabled node/channel만 Memory 사용 |
| MEM-TC-E2E-014 | Concurrent sends | UI 직렬화 또는 one typed conflict, 순서 손상 없음 |
| MEM-TC-E2E-015 | Timeout then late Worker result | Reset/delete/new lifecycle에 write하지 않음 |
| MEM-TC-E2E-016 | Public session then login | Internal session으로 자동 병합되지 않음 |
| MEM-TC-E2E-017 | Authenticated session then logout | Public endpoint에서 history 미노출 |
| MEM-TC-E2E-018 | Concurrent summary cache miss | Provider/usage 한 번 |
| MEM-TC-E2E-019 | Memory store unavailable | Surface별 failure/degraded contract |
| MEM-TC-E2E-020 | Legacy chatbot with private Knowledge | Provenance 없는 log history 비활성화 + guided migration |
| MEM-TC-E2E-021 | StartTurn commit 후 Gateway crash | Dispatcher가 pending job을 복구하고 active turn이 영구 정체되지 않음 |
| MEM-TC-E2E-022 | Broker publish ack 유실 | 동일 dispatch/turn으로 중복 제거, Memory write 한 번 |
| MEM-TC-E2E-023 | Old/new Worker rolling pool | Target task는 capable queue/Worker만 실행, wrong Worker는 side effect 전 거부 |
| MEM-TC-E2E-024 | Authenticated cross-site mutation | CSRF/Origin/Fetch Metadata gate가 run/reset/delete를 차단 |
| MEM-TC-E2E-025 | Public create/run flood | Rate/concurrency/cost gate가 DB/provider 증폭 전에 차단 |
| MEM-TC-E2E-026 | Public token create response lost | Same-key retry가 추가 grant 없이 같은 안전 응답을 복구 |
| MEM-TC-E2E-027 | Summary lease old owner late completion | Fencing generation이 stale summary를 차단하고 actual usage는 정확히 한 번 reconcile |
| MEM-TC-E2E-028 | Node projection 후 downstream failure | Projection이 다음 turn Memory에 포함되지 않음 |
| MEM-TC-E2E-029 | Close then transcript | Runtime context 차단, scoped retention transcript 허용 |
| MEM-TC-E2E-030 | Public delete and grant revoke | Purge receipt로만 terminal purge 상태 확인 |
| MEM-TC-E2E-031 | CompleteTurn entry write failure | Turn/Session/entry/outbox 전체 rollback, provider 재호출 없음 |
| MEM-TC-E2E-032 | Lease claim 후 provider start 전 Worker crash | Claim expiry 뒤 재승인, current permission 재검증 |
| MEM-TC-E2E-033 | Provider start marker 후 timeout | 자동 provider 재호출 없음, outcome reconciliation |
| MEM-TC-E2E-034 | Public purge retry 7일 초과 | Terminal failure + dead-letter/alert, receipt로 결과 확인 |
| MEM-TC-E2E-035 | Summary model price unavailable | Window/failure policy, summary provider 미호출 |
| MEM-TC-E2E-036 | Public Chatbot with login cookie and private KB | Public-only answer, private entry/source 비노출 |
| MEM-TC-E2E-037 | Authenticated internal Chatbot with private KB | 별도 내부 이용 권한과 current KB permission을 모두 통과한 경우에만 private evidence 사용 |
| MEM-TC-E2E-038 | Public Access Grant against internal route | 인증/권한 승격 없이 거부, session namespace 격리 |
| MEM-TC-E2E-039 | Active deployment version replaced mid-session | 기존 session 자동 rebind 없음, surface별 new-session contract |
| MEM-TC-E2E-040 | Legal hold purge then hold release | completed_with_hold에는 purged 없음, public status/receipt 재개 없이 별도 compliance erasure 후 정확히 한 번 purged |
| MEM-TC-E2E-041 | Reset lifecycle audit replay | old reset/revoke + new created/issued만 한 번, closed 중복 없음 |
| MEM-TC-E2E-042 | Private result through transform/code chain | 모든 dependency union 유지, 하나 revoke 시 entry 전체 제외 |
| MEM-TC-E2E-043 | New answer derived from private Memory Context | Prior entry dependency가 새 answer/entry에도 상속되고 source revoke 뒤 둘 다 context에서 제외 |

## Security Adversarial Cases

- MEM-TC-SEC-001: Public token 추측/replay/cross-slug/cross-deployment 사용.
- MEM-TC-SEC-002: URL/Referer/browser log를 통한 token 유출, persistent localStorage/cookie 사용 회귀와 sessionStorage의 XSS 위협.
- MEM-TC-SEC-003: Client-supplied internal session, subject, dependency, authorization-safe reference와 decision revision spoofing.
- MEM-TC-SEC-004: Past Memory의 system override/prompt injection이 system instruction으로 승격되는 공격.
- MEM-TC-SEC-005: Private source name/path와 denied count를 summary/transcript/error로 유도하는 공격.
- MEM-TC-SEC-006: Secret/API key/credential을 기억하라는 요청과 hostile tool response.
- MEM-TC-SEC-007: Permission revoke와 Build/summary/main provider 호출 사이의 TOCTOU.
- MEM-TC-SEC-008: Deleted user/session의 queued write retry.
- MEM-TC-SEC-009: Oversized turn/dependency로 token/cost amplification.
- MEM-TC-SEC-010: Provenance graph cycle/depth/duplicate/unknown kind.
- MEM-TC-SEC-011: Request/attempt/execution ID collision과 다른 tenant idempotency key 재사용.
- MEM-TC-SEC-012: Malformed Unicode/Markdown/HTML/JSON으로 redaction, serialization 또는 UI XSS 우회.
- MEM-TC-SEC-013: Cookie `SameSite=None`, forged Origin, missing/invalid CSRF token과 cross-site form/fetch mutation.
- MEM-TC-SEC-014: Public Conversation route에 전역 credentialed CORS, wildcard ACAO, `Access-Control-Allow-Credentials`, Authorization preflight 또는 deployment parent allowlist가 누출되어 external direct JavaScript가 grant 응답을 읽는 회귀.
- MEM-TC-SEC-015: Public session/grant churn, distributed source rotation과 concurrent run을 통한 rate/cost limit 우회.
- MEM-TC-SEC-016: Token response/cache/browser storage 유출과 malformed version prefix/timing oracle.
- MEM-TC-SEC-017: Expired purge receipt 또는 다른 deployment receipt로 session/purge state 추론.
- MEM-TC-SEC-018: Access Grant를 execution subject, LLM credential principal, billing principal 또는 audit actor로 승격.
- MEM-TC-SEC-019: App/deployment owner를 public audit actor나 private Knowledge execution subject로 합성.
- MEM-TC-SEC-020: Forged optional dependency flag로 denied/private source lineage 제거.
- MEM-TC-SEC-021: Stale authorization decision revision 또는 source ACL revision replay.
- MEM-TC-SEC-022: Expired/wrong-purpose/wrong-pricing ProviderExecutionCapability로 context materialization, provider call 또는 budget reservation.
- MEM-TC-SEC-022A: Client/Memory/Budget가 credential principal이나 permission decision revision을 위조하거나 stale capability identity를 새 revision으로 재해석.
- MEM-TC-SEC-023: Active deployment pointer 교체로 old session을 attacker-selected graph/version에 자동 rebind.

## Rolling Deployment And Migration Tests

- MEM-TC-MIG-001: Old Client + new Gateway + old Worker는 legacy workflow를 실행하되 unsafe history Memory를 사용하지 않는다.
- MEM-TC-MIG-002: Old Client + new Gateway + new Worker는 deployment storage generation에 따라 하나의 경로만 선택한다.
- MEM-TC-MIG-003: New Client + old Gateway 조합은 release ordering/contract version에서 지원하지 않는다.
- MEM-TC-MIG-004: New Gateway + old Worker는 capability/preflight로 새 Memory deployment 실행을 차단한다.
- MEM-TC-MIG-005: Additive schema migration 중 기존 WorkflowRun/NodeRun을 보존한다.
- MEM-TC-MIG-006: Rollback 후 new storage-generation session을 legacy writer가 변경하지 않는다.
- MEM-TC-MIG-007: Dual-read 기간에도 new entry와 legacy log를 하나의 context로 중복 병합하지 않는다.
- MEM-TC-MIG-008: Single-LLM legacy chatbot은 명확한 mapping을 사용자에게 제안할 수 있다.
- MEM-TC-MIG-009: Multi-LLM/ambiguous output legacy chatbot은 자동 migration하지 않는다.
- MEM-TC-MIG-010: Legacy private Knowledge 권한 회수 후 log-derived answer가 context에 재등장하지 않는다.
- MEM-TC-MIG-011: Deployment version 변경은 conversation schema/Memory contract compatibility를 검사한다.
- MEM-TC-MIG-012: Common queue에 old/new Worker가 섞여 있어도 target task는 versioned routing으로 old Worker에 전달되지 않는다.
- MEM-TC-MIG-013: 잘못 라우팅된 target task는 old/incompatible Worker가 side effect 전에 reject/dead-letter하고 safe reason만 남긴다.
- MEM-TC-MIG-014: Preflight 통과 후 Worker pool이 교체되어도 runtime capability guard가 fail-closed 한다.
- MEM-TC-MIG-015: Existing session은 active deployment 변경 후에도 old pinned version을 유지하고 자동 migration하지 않는다.
- MEM-TC-MIG-016: Public old-version session은 version 존재를 노출하지 않고 explicit new conversation으로만 전환한다.
- MEM-TC-MIG-017: Authenticated old-version session은 typed conflict와 safe new-session action을 반환하고 transcript를 새 session에 자동 복사하지 않는다.
- MEM-TC-MIG-018: Additive migration 적용 전 public lifecycle feature activation은 누락된 schema capability로 startup에서 실패하고 적용 뒤 성공한다. Migration filename, revision ID 또는 당시의 latest head를 하드코딩하지 않고 실제 required table·column 집합을 검증한다.
- MEM-TC-MIG-019: Runtime additive revision은 Turn replay key/grant provenance, Entry source-free proof와 Workflow execution admission table을 생성한다. 새 runtime binding 또는 admission data가 있으면 downgrade가 fail-closed하고 명시적 보존·배출 뒤 foundation parent로 round-trip 한다.

## Performance And Reliability Tests

- MEM-TC-PERF-001: 최대 turn/content/dependency에서 context build p95 baseline을 측정한다.
- MEM-TC-PERF-002: Session append와 summary lease/CAS contention을 측정한다.
- MEM-TC-PERF-003: Bulk authorization이 dependency별 N+1 query를 만들지 않는다.
- MEM-TC-PERF-004: Expired/invalidated 대량 entry에서도 bounded index query를 유지한다.
- MEM-TC-PERF-005: Summary cache hit/miss와 provider amplification을 측정한다.
- MEM-TC-PERF-006: Purge batch가 active session latency와 DB lock을 과도하게 늘리지 않는다.
- MEM-TC-PERF-007: Memory adapter timeout/circuit behavior가 Workflow worker pool을 고갈시키지 않는다.

## Property And Fuzz Tests

- MEM-TC-FUZZ-001: 임의 turn/lifecycle state 전이에서 aggregate invariant를 유지한다.
- MEM-TC-FUZZ-002: 임의 retry/duplicate/reorder delivery에서 logical turn은 최대 하나다.
- MEM-TC-FUZZ-003: Node config integer/enum/channel/ID boundary를 검증한다.
- MEM-TC-FUZZ-004: Dependency graph cycle/duplicate/unknown/oversized metadata를 검증한다.
- MEM-TC-FUZZ-004A: Envelope version/completeness/missing-vs-empty/tenant mismatch와 conflicting duplicate를 검증하고 private/sensitive write를 fail-closed 한다.
- MEM-TC-FUZZ-005: Token/context 분할 결과가 항상 configured upper bound 이하다.
- MEM-TC-FUZZ-006: Malformed Unicode/Markdown/JSON이 redaction/serialization boundary를 우회하지 않는다.

## Observability Assertions

- MEM-TC-OBS-001: Read/write/summary/lifecycle decision은 safe typed reason으로 추적된다.
- MEM-TC-OBS-002: Raw Memory/transcript/prompt/token/private source는 audit/trace/metric에 없다.
- MEM-TC-OBS-003: Summary usage는 Memory purpose로 비용 집계된다.
- MEM-TC-OBS-004: Denied/revoked source 수는 boolean/bucket만 사용한다.
- MEM-TC-OBS-005: Store/authorization/summarizer/budget latency와 failure를 분리한다.
- MEM-TC-OBS-006: Generation reservation/commit/reconcile 상태를 raw provider response 없이 추적한다.
- MEM-TC-OBS-007: Retry/conflict/late-write count는 safe reason과 bucket으로만 기록한다.
- MEM-TC-OBS-008: Organization-scoped memory event는 `organization_id`를 포함해 관리자 audit 조회에 나타나며 다른 tenant에서는 숨겨진다.
- MEM-TC-OBS-009: Dispatch/summary retry는 logical terminal transition당 operational event/trace를 한 번만 만들고 AuditLog를 생성하지 않는다.
- MEM-TC-OBS-010: Raw context handle mapping, grant/purge token과 CSRF token은 observability payload에 없다.
- MEM-TC-OBS-011: 정상 turn/summary 상태는 operational trace/metric만 만들고 AuditLog row를 생성하지 않는다.
- MEM-TC-OBS-012: Session/grant lifecycle만 `memory.session.*`/`memory.grant.*` AuditLog를 만들며 permission/policy/provider/workflow 사건은 기존 canonical action과 중복되지 않는다.
- MEM-TC-OBS-013: Public create/close/reset/delete request action의 exact target/action/count/status와 `actor_type='public'`, physical purge/compliance completion의 `actor_type='system'`을 검증한다. 모두 `actor_id=null`이고 owner fallback이 없다.
- MEM-TC-OBS-014: completed_with_hold/terminal_failure는 operational/compliance event와 alert만 만들고 `memory.session.purged`를 만들지 않는다.
- MEM-TC-OBS-015: ProviderExecutionCapability는 safe opaque reference/revision만 관측하고 credential, raw scope/token과 private source를 남기지 않는다.
- MEM-TC-OBS-016: V1 create/reset/close/delete lifecycle은 `memory.grant.rotated`를 발행하지 않고 issued/revoked cardinality만 계약대로 기록한다.

## Requirement Traceability

### MBA-316 automated foundation evidence

MBA-316은 production composition을 활성화하지 않고 아래 persistence/lifecycle subset을 자동화한다. 이 표는 전체 Conversation Memory 기능이 완료됐다는 의미가 아니며, Access Grant/API, Runtime provenance/context, summary/provider/usage와 physical purge 사례는 후속 이슈의 테스트로 남는다.

| Test target | 구현 evidence | 연결 사례 |
| --- | --- | --- |
| `apps/memory/tests/architecture/test_boundaries.py` | Domain/application framework 독립성, import side effect 부재, Memory ORM direct access 제한 | MEM-TC-ARCH-001, 002, 006, 008 |
| `apps/memory/tests/domain/test_conversation.py` | Session/Turn revision, active turn, clock expiry late-write 차단, hash-only replay identity, purge receipt 8일 상한, 모든 terminal outcome의 absorbing transition property, dispatch fencing·claim expiry recovery | MEM-TC-DOM-001, 003~013, 015 및 MEM-TC-DOM-016의 dispatch process subset |
| `apps/memory/tests/application/test_lifecycle.py` | Create/Start/Complete/Close/Delete-pending UoW, same-request replay, matching terminal CompleteTurn replay와 mismatch conflict, clock expiry write 차단, dispatch insert failure rollback, unknown outcome fail-closed | MEM-TC-APP-002, 008~012, 015, 016, 040, 041의 mutation 차단, 043의 tombstone/purge-job 기반 |
| `apps/memory/tests/application/test_dispatch.py`, `test_dispatch_reconciliation.py` | Claim/publish/publish-failure/expired-recovery command만 상태를 전이하고 periodic due scan이 pending/reconcile/expired/terminal cleanup을 bounded 처리하며 stale conflict를 item 단위로 격리 | MEM-TC-APP-013A, 017, 017A, 017B, 018 |
| `apps/memory/tests/adapters/test_schema.py`, `test_repository.py` | Memory model/readiness, nullable reference의 tenant-scoped composite FK와 Access Grant canonical binding FK, lifecycle/turn/entry/dispatch CAS와 terminal/fencing check, projection별 암호화 envelope, safe DB error 변환 | MEM-TC-DB-001, 004, 007, 008, 010, 023의 schema/repository subset |
| `apps/memory/tests/adapters/test_disposable_postgres.py` | 실제 PostgreSQL clean upgrade와 Memory foundation/Public capability replay revision별 Alembic model drift·round-trip check, Access Grant binding mismatch DB rejection, legacy Run/NodeRun 보존 downgrade, concurrent StartTurn 단일 승자, StartTurn partial-write rollback, dispatch claim/publish | MEM-TC-DB-002, 007, 013, 014, 017 및 MEM-TC-MIG-005 |

### MBA-318 protected-resource completion evidence

MBA-318은 durable Memory resource reference, current authorization과 provider 외부 I/O 경계를 포함하므로 `docs/engineering/protected-resource-feature-completion.md`를 적용한다.

| 경계 | 상태 | Evidence와 follow-up review |
| --- | --- | --- |
| 저장 | 완료 | `StartTurnUseCase`, `SqlAlchemyConversationMemoryRepository`, Workflow execution admission과 context plan/lease/provider-attempt CAS; candidate/entry query는 approved, non-invalidated, non-expired reference만 선택하고 raw content를 Build에서 읽지 않음 |
| 관리 API/UI | 해당 없음 | MBA-318은 기존 Public lifecycle/Workflow 실행 경로를 연결하며 새 관리 API/UI를 추가하지 않음 |
| Preflight | 완료 | Gateway public runtime은 current organization/deployment/grant, stored fingerprint key version과 completed-turn cap을 durable write 전에 검증하고 exact retry만 기존 Turn 복구로 분리. 월 예산 이중 fence는 MBA-385 후속 |
| Runtime/background | 완료 | exact `conversation-memory-v1` routing/activation, current grant/capability/admission fence, bounded reference context, provider-start/usage marker와 autonomous dispatch due reconciliation을 application/provider/composition/deployment contract 테스트로 검증 |
| Lifecycle | 완료 | 결정적 pre-provider 오류는 context/Turn/admission을 함께 terminalize하고 transient adapter/storage failure는 retryable하게 보존; revoked/expired redelivery, checkpoint/historical no-I/O reconciliation과 additive migration downgrade guard 유지 |
| Audit/redaction | 완료 | completed transcript는 AAD-bound approved display만 반환하고 failed/cancelled content와 model projection을 배제; safe reason과 raw Memory/token/provider payload 비영속 검증. 전용 durable execution journal은 MBA-386 후속 |
| 테스트 | 완료 | Memory public runtime/security/lifecycle, Gateway public API/publisher/composition/deployment, Workflow execution/provider/composition 테스트가 key rotation, session-bound cursor, runtime-disabled historical decrypt, 0/99/100 turn cap과 race, 50+1 paging/tamper, dispatch due reconciliation, queue 및 deterministic/transient failure matrix를 포함; disposable PostgreSQL contract는 전용 CI 증거 |

Disposable PostgreSQL evidence는 `NODEASE_RUN_DISPOSABLE_DB_TEST=1`인 전용 CI job에서 한 번 실행하고 일반 `memory-tests` job에서는 제외한다. MEM-TC-DB-003/005~007/009/011~012/015~026과 lifecycle audit/outbox cardinality는 관련 application adapter가 구현되기 전 완료로 표시하지 않는다.

| Requirement range | Primary test sections |
| --- | --- |
| MEM-REQ-001~007 | Architecture Boundary, Session Aggregate, Lifecycle, Gateway/API |
| MEM-REQ-010~019 | Session Aggregate, Start/Complete Turn, Dispatch, PostgreSQL, E2E concurrency |
| MEM-REQ-020~026 | Node Configuration, Workflow Runtime, Client, Migration |
| MEM-REQ-030~039 | Authorization/Provenance, Runtime Provenance, Context Lease, Security |
| MEM-REQ-040~049 | Gateway/API, Access Grant repository, Public/Auth E2E, Security |
| MEM-REQ-050~059 | BuildMemoryContext, Context Lease, Summary Process, Summarizer/Budget, Observability |
| MEM-REQ-060~069 | Lifecycle, Client Transcript, Purge Receipt, Privacy/Security, Observability |
| MEM-REQ-070~079 | Rolling Deployment And Migration, Dispatch Processing, Workflow Runtime, Legacy E2E |
| MEM-REQ-080~085 | Context Materialization, Provider Attempt Reliability, Summary Pricing, Security/E2E |
| MEM-REQ-086~096 | Version Binding, Principal/Capability, Purge, Audit Cardinality, Cross-Domain E2E |
| MEM-NFR-001~009 | Architecture Boundary, API cache/idempotency/CSP, Performance/Reliability, Property/Fuzz |
