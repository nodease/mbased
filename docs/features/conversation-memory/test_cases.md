# Conversation Memory Test Cases

Status: Draft

## Test Principles

- Public Chatbot은 client-held history와 content-free server logging을 검증한다.
- Durable Session/Turn/Entry/summary/purge test는 authenticated internal Chatbot 후속 target 증거로 유지한다.
- Client history는 신뢰하지 않고 role, exact field, order, turn, byte와 token limit를 서버가 다시 검증한다.
- Secret, raw private source, Public history와 prompt/completion을 assertion failure/output에 남기지 않는다.
- 같은 동작을 계층별로 반복하지 않고 shared pure policy, Gateway boundary, LLM message assembly, logger persistence와 Client projection을 각각 한 번 검증한다.

## Public Client-Held History Tests

- MEM-TC-PUB-001: 빈 history와 완료된 user/assistant pair를 허용한다.
- MEM-TC-PUB-002: system/developer/tool role, extra field, 빈 content, assistant-first, 연속 role과 미완성 turn을 거부한다.
- MEM-TC-PUB-003: 20 turn은 허용하고 21 turn은 provider dispatch 전에 거부한다.
- MEM-TC-PUB-004: 현재 inputs와 history가 4,096 token을 넘으면 가장 오래된 완료 turn만 제거한다.
- MEM-TC-PUB-004A: 정제 marker, JSON serialization과 untrusted framing까지 완료한 최종 history projection이 current inputs의 잔여 4,096-token 예산을 넘으면 가장 오래된 완료 pair만 제거하고 provider와 RAG가 동일 projection을 사용한다.
- MEM-TC-PUB-005: 현재 inputs만으로 4,096 token을 넘으면 history를 모두 버리고 진행하지 않고 거부한다.
- MEM-TC-PUB-006: Gateway는 bounded history를 별도 execution context로 전달하고 workflow business inputs를 오염시키지 않는다.
- MEM-TC-PUB-007: Public Chatbot은 memory_mode=false, conversation_id=null이며 legacy control을 거부한다.
- MEM-TC-PUB-008: LLMNode는 history를 system message 뒤, 현재 user prompt 앞에 넣고 legacy DB memory query를 호출하지 않는다.
- MEM-TC-PUB-009: WorkflowRun/NodeRun/Trace payload에는 current input, history, prompt와 completion 원문이 저장되지 않는다.
- MEM-TC-PUB-010: Client는 welcome/error/pending message를 제외하고 최신 완료 20 turn만 보낸다.
- MEM-TC-PUB-011: Public lifecycle route는 API router에 등록되지 않고 no-store/no-referrer 404를 반환한다.
- MEM-TC-PUB-012: Public history 응답은 CORS grant를 제공하지 않고 validation error에 원문을 반사하지 않는다.
- MEM-TC-PUB-013: History leaf content는 한 번만 정제하며 redaction marker를 다시 검사해 정상 형제 turn 전체를 지우지 않는다.
- MEM-TC-PUB-014: RAG 검색어는 현재 질문과 가장 최근 완료 turn의 정제된 history를 1,000자 안에서 사용하고, 오래된 초과 turn은 pair 단위로 제외한다.
- MEM-TC-PUB-015: History가 없으면 기존 현재 질문 RAG 검색어를 그대로 유지하고, history 유무가 Knowledge candidate·authorization 결과를 바꾸지 않는다.
- MEM-TC-PUB-016: `/chat/` OPTIONS와 redirect 응답도 `/chat`과 동일하게 CORS grant 없이 no-store/no-referrer 경계를 적용한다.

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

- MEM-TC-DOM-040: Authenticated source는 current subject와 organization/resource authorization이 확인될 때만 read를 허용한다.
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
- MEM-TC-DOM-051: Authenticated internal subject revision이 없거나 stale이면 Memory read를 fail-closed한다.
- MEM-TC-DOM-052: Dependency optional flag를 주입해도 V1 policy는 값·활성 control dependency를 모두 필수로 평가한다.
- MEM-TC-DOM-053: Membership/team/direct permission/source ACL/lifecycle 변경은 authorization decision revision을 바꾸고 stale result를 거부한다.
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
- MEM-TC-APP-014: Publish 성공 응답 유실과 duplicate publish는 같은 dispatch/turn을 재사용한다.
- MEM-TC-APP-014A: Durable Workflow admission 전 duplicate publish는 admission에서 제거되고 admission 이후 outcome unknown은 arbitrary execution 재실행 없이 Workflow reconciliation 또는 safe terminal failure로 닫힌다.
- MEM-TC-APP-015: Required CompleteTurn 실패는 success/applied를 반환하지 않고 durable execution result로 CompleteTurn만 재시도한다. 최초 terminal commit 뒤 응답 유실 retry는 outcome, expected predecessor version, assistant entry와 protected content identity가 모두 일치할 때 canonical result를 재생하고, 하나라도 다르면 conflict로 거부한다.
- MEM-TC-APP-016: CompleteTurn의 Turn terminal, Session active-turn/content revision, final entry/projection과 required outbox는 모두 commit되거나 모두 rollback된다.

### Dispatch Processing

- MEM-TC-APP-017: Claim/publish/admission observation/reconciliation은 expected state/version과 fencing generation을 검증한다.
- MEM-TC-APP-017A: Stale dispatch state/version/generation은 `memory.dispatch_state_conflict`로 거부되고 adapter raw error를 노출하지 않는다.
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
- MEM-TC-APP-029J: Provider capability의 organization/deployment version/node invocation/purpose/model/pricing revision/expiry 중 하나라도 lease와 다르면 raw context와 provider 호출을 모두 거부한다.
- MEM-TC-APP-029K: Lease claim은 실제 materialized entry/summary와 정확히 대응하는 RuntimeDataDependencyEnvelope를 반환하고 excluded/stale source를 envelope에 남기지 않는다.

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
- MEM-TC-APP-044: Authenticated purge가 Session subject binding, Turn content, final/provisional entry·projection, summary, sensitive dependency, raw/materialized context cache·plan과 transcript/result를 남기지 않고 content-free bounded operational tombstone만 허용한다.
- MEM-TC-APP-045: Legal hold가 있어도 runtime read는 즉시 차단된다.
- MEM-TC-APP-045A: Legal hold content는 runtime/provider query에서 격리되고 일반 operator response는 내부 hold 사유를 노출하지 않는다.
- MEM-TC-APP-046: Temporary authorization outage는 current read만 차단하고 entry를 영구 invalidation하지 않는다.
- MEM-TC-APP-047: Authenticated Delete 직후 session 접근이 차단되고 Purge Job은 canonical organization/deployment/session scope로 terminal state까지 수렴한다.
- MEM-TC-APP-048: Authenticated Close는 run/reset mutation을 거부하되 권한 있는 transcript read, privacy delete와 same-key close replay를 허용한다.
- MEM-TC-APP-049: Dispatch/Summary Job, Provider Attempt, Context Lease와 idempotency record는 purge 뒤 content/private reference 없이 bounded opaque state/timestamp/safe reason만 남고 retention 만료 후 삭제된다. Idempotency parent 삭제는 종속 encrypted replay를 cascade하고 scope/key uniqueness claim을 해제한다.
- MEM-TC-APP-050: Audit/usage record는 별도 retention을 유지해도 raw content/token/hash/prompt/private source를 포함하지 않으며 purge job/receipt는 receipt expiry 뒤 삭제된다.
- MEM-TC-APP-050A: Live store/cache/replay 또는 backup/export erasure marker 하나라도 unknown/partial이면 purge를 completed/purged로 기록하지 않고 retry/terminal failure 또는 compliance isolation로 닫는다.
- MEM-TC-APP-051: `completed_with_hold`와 `terminal_failure`는 `memory.session.purged`를 만들지 않고, hold 해제 후 별도 compliance erasure 완료가 한 번만 purged를 만든다.
- MEM-TC-APP-052: Authenticated create/close/reset/delete request는 실제 user actor, asynchronous physical purge/compliance completion은 system actor를 사용하며 retry에서 중복되지 않는다.
- MEM-TC-APP-053: Authenticated lifecycle audit actor는 current user이며 app/deployment owner, execution, credential 또는 billing principal로 대체되지 않는다.
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

### Authorization Adapters

- MEM-TC-ADP-001: Organization membership/subject 상태를 canonical scope로 평가한다.
- MEM-TC-ADP-002: Knowledge direct/team/manager와 source ACL two-gate contract를 bulk 평가한다.
- MEM-TC-ADP-003: Authenticated subject와 source-managed authorization을 모두 적용한다.
- MEM-TC-ADP-004: Connector/tool/subworkflow lifecycle과 current access를 평가한다.
- MEM-TC-ADP-005: Dependency별 N+1 query 없이 bounded bulk/fallback을 사용한다.
- MEM-TC-ADP-006: Raw ACL fact와 denied identity를 Memory result에 노출하지 않는다.
- MEM-TC-ADP-007: Source adapter는 decision/principal kind/authorization decision revision/resource revision/policy revision/evaluated_at을 반환한다.
- MEM-TC-ADP-008: Source adapter timeout, malformed revision 또는 partial bulk response는 누락 dependency를 allow하지 않는다.
- MEM-TC-ADP-009: Authenticated authorization은 source domain의 current subject/decision revision contract를 반환한다.
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

- MEM-TC-API-001: `conversation.history`가 없는 Public Chatbot 요청은 `conversation.history_required`로 거부한다.
- MEM-TC-API-002: malformed history는 safe `conversation.*` 422를 반환하고 request content를 echo하지 않는다.
- MEM-TC-API-003: Public history request/response는 no-store/no-referrer이며 ACAO/credentials header가 없다.
- MEM-TC-API-004: Public lifecycle create/close/reset/delete/transcript/turn/purge route는 등록되지 않는다.
- MEM-TC-API-005: WEBAPP/WIDGET single-run과 authenticated internal Chatbot route는 기존 계약을 유지한다.
- MEM-TC-API-006: Public Chatbot history path가 execution subject를 app owner나 login cookie로 합성하지 않는다.
- MEM-TC-API-007: legacy memory_mode/conversation_id는 Public history path에서 거부되지만 authenticated internal control은 subject-bound namespace를 유지한다.
- MEM-TC-API-008: `/run-public/{slug}/chat/` preflight는 404이고 POST redirect를 포함한 모든 응답에서 CORS grant를 제거한다.

## Workflow Runtime Tests

- MEM-TC-RUN-001: Node Memory config가 graph save/load/copy/deployment snapshot round-trip을 보존한다.
- MEM-TC-RUN-002: Draft 변경이 active deployment Memory policy를 바꾸지 않는다.
- MEM-TC-RUN-003: Explicit user input/final output mapping이 없으면 activation/runtime을 fail-closed 한다.
- MEM-TC-RUN-004: Multi-answer graph에서 runtime이 final output을 추측하지 않는다.
- MEM-TC-RUN-005: Knowledge/tool/subworkflow dependency가 transform/LLM/final answer까지 합집합으로 전파된다.
- MEM-TC-RUN-006: Provenance-incomplete code/custom result의 private/sensitive write를 거부한다.
- MEM-TC-RUN-007: Enabled node만 Memory를 사용하고 channel이 섞이지 않는다.
- MEM-TC-RUN-008: Context와 Knowledge block은 untrusted label/order를 유지한다.
- MEM-TC-RUN-009: Main provider adapter가 호출 직전 context lease를 provider attempt로 claim하고 current authorization을 재검증한 operation에서 raw context를 materialize한다.
- MEM-TC-RUN-010: Duplicate Worker delivery가 Memory-owned turn/entry/summary/usage mutation을 중복 생성하지 않는다. Arbitrary node/tool side effect idempotency는 Workflow domain contract로 별도 검증한다.
- MEM-TC-RUN-011: Subworkflow는 explicit bounded context/channel만 상속하고 parent table을 직접 조회하지 않는다.
- MEM-TC-RUN-012: Unsupported contract/version Worker가 새 deployment를 실행하지 않는다.
- MEM-TC-RUN-013: Worker는 contract/storage/minimum capability mismatch를 외부 node side effect 전에 거부한다.
- MEM-TC-RUN-014: Dedicated/versioned queue의 old Worker가 target task를 소비하지 못하고 잘못 라우팅된 task도 runtime guard가 거부한다.
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
- MEM-TC-RUN-025: Public absolute deadline이 Celery hard deadline보다 이르면 runtime deadline으로 유지되고 Knowledge/provider 호출 직전 만료를 거부한다.
- MEM-TC-RUN-025A: 공통 external-effect executor는 claim 전과 invoke 직전 deadline을 검사하고, claim 뒤 만료를 failed-before-effect/stop으로 terminalize하며 write 및 read-only provider를 호출하지 않는다.
- MEM-TC-RUN-026: Public history reference는 canonical deployment/preflight와 Knowledge sync 뒤 한 번만 소비한다.
- MEM-TC-RUN-027: Public history를 소비한 뒤 일반 또는 external-effect retry 오류가 발생해도 Celery retry를 예약하지 않는다.
- MEM-TC-RUN-028: WorkflowNode child는 public actor/suppression/deadline을 유지하되 parent raw history/reference/consumer binding을 상속하지 않는다.
- MEM-TC-RUN-029: 같은 node ID가 parent와 child graph에 존재해도 child LLM/RAG에는 parent public history가 전달되지 않는다.

## Client Tests

- MEM-TC-UI-001: 첫 질문은 `history: []`를 보낸다.
- MEM-TC-UI-002: 성공한 user/assistant pair만 다음 요청 history에 포함한다.
- MEM-TC-UI-003: welcome, error, pending user와 citation metadata는 history에 포함하지 않는다.
- MEM-TC-UI-004: 21번째 완료 turn부터 가장 오래된 turn을 Client request에서 제외한다.
- MEM-TC-UI-005: refresh 또는 새 tab은 Public history를 복구하지 않는다.
- MEM-TC-UI-006: localStorage/sessionStorage에 history 또는 conversation ID를 기록하지 않는다.
- MEM-TC-UI-007: 실패 status나 empty final preview 대신 표시한 fallback assistant text는 다음 history에 포함하지 않는다.
- MEM-TC-UI-008: 배포 UI는 nested Loop LLM을 canonical container path로 구분하고 같은 node ID가 다른 path에 있어도 정확한 consumer를 저장한다.

## End-To-End Matrix

| ID | Scenario | Expected |
| --- | --- | --- |
| MEM-TC-E2E-001 | Public first turn | Empty history, server conversation row 없음 |
| MEM-TC-E2E-002 | Public three completed turns | Client history가 current prompt 앞에 전달됨 |
| MEM-TC-E2E-003 | Public refresh/new tab | 과거 history 자동 복구 없음 |
| MEM-TC-E2E-004 | 21 public turns | Client는 최신 20개만 전송, server도 상한 검증 |
| MEM-TC-E2E-005 | Oversized history | 오래된 완료 turn 단위 제거 |
| MEM-TC-E2E-006 | Forged system/tool role | Provider 전에 422 |
| MEM-TC-E2E-007 | Public with login cookie/private KB | Public-only 권한, private subject 합성 없음 |
| MEM-TC-E2E-008 | Public provider success | Run/Node/Trace content 원문 없음 |
| MEM-TC-E2E-009 | Authenticated internal legacy run | Subject-bound namespace 유지 |
| MEM-TC-E2E-010 | Future internal durable Memory | 별도 후속 issue/test matrix |

## Security Adversarial Cases

- MEM-TC-SEC-001: system/developer/tool role과 extra field 주입.
- MEM-TC-SEC-002: assistant-first, 연속 user, dangling user와 21-turn payload.
- MEM-TC-SEC-003: history에 subject/organization/permission/credential 지시 삽입.
- MEM-TC-SEC-004: login cookie 또는 Authorization으로 Public private-resource 권한 승격 시도.
- MEM-TC-SEC-005: request/history marker가 API error, task repr, WorkflowRun/NodeRun/Trace/log에 남는 회귀.
- MEM-TC-SEC-006: legacy memory_mode/conversation_id로 server execution-log memory를 다시 활성화하는 시도.
- MEM-TC-SEC-007: oversized UTF-8/malformed JSON과 token counter failure.
- MEM-TC-SEC-008: Public lifecycle/grant/transcript route probe.
- MEM-TC-SEC-009: external Origin/CORS credentialed read와 cache/referrer 노출.
- MEM-TC-SEC-010: 정제 marker를 포함한 history를 다시 정제해 전체 serialized history가 marker 하나로 치환되는 회귀.

## Rolling Deployment And Migration Tests

- MEM-TC-MIG-001: 새 Client는 새 Gateway와 함께 history envelope을 사용한다.
- MEM-TC-MIG-002: 새 Gateway는 envelope 없는 Public Chatbot을 fail-closed해 legacy server memory로 fallback하지 않는다.
- MEM-TC-MIG-003: WEBAPP/WIDGET와 authenticated internal legacy run은 영향받지 않는다.
- MEM-TC-MIG-004: Public lifecycle router 제거 후 기존 dormant table/data는 migration에서 파괴하지 않는다.
- MEM-TC-MIG-005: Public current tree에는 durable Memory runtime 연결이 없고 authenticated internal surface에서만 후속 활성화한다.
- MEM-TC-MIG-006: Strict rollout은 consumer mapping 없는 legacy Public Chatbot 재활성화를 knowledge/secret/schedule/active-pointer/commit 전에 차단하고 Compatibility rollout은 기존 동작을 유지한다.

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

- MEM-TC-OBS-001: Public run은 status, duration, deployment/workflow/run safe ID와 usage만 저장한다.
- MEM-TC-OBS-002: WorkflowRun input/output, NodeRun input/output/process_data와 Trace payload에 content marker가 없다.
- MEM-TC-OBS-003: Celery task args/kwargs representation은 redacted다.
- MEM-TC-OBS-004: Public AsyncResult는 소비 직후 forget되며 장애 시 configured result TTL을 넘지 않는다.
- MEM-TC-OBS-005: validation/provider/logging error는 history/prompt/completion을 출력하지 않는다.
- MEM-TC-OBS-006: authenticated internal observability는 후속 retention/redaction 계약을 따른다.

## Requirement Traceability

### MBA-316 automated foundation evidence

MBA-316/317은 production Public composition을 활성화하지 않는 persistence/lifecycle foundation을 자동화했다. 아래 표의 Access Grant/Public replay 사례는 dormant legacy foundation의 보존 증거이며 ADR-0074의 active Public 계약이나 MBA-318 merge gate가 아니다. Authenticated internal Memory는 후속 이슈에서 필요한 test만 선별 재사용한다.

| Test target | 구현 evidence | 연결 사례 |
| --- | --- | --- |
| `apps/memory/tests/architecture/test_boundaries.py` | Domain/application framework 독립성, import side effect 부재, Memory ORM direct access 제한 | MEM-TC-ARCH-001, 002, 006, 008 |
| `apps/memory/tests/domain/test_conversation.py` | Session/Turn revision, active turn, clock expiry late-write 차단, hash-only replay identity, purge receipt 8일 상한, 모든 terminal outcome의 absorbing transition property, dispatch fencing·claim expiry recovery | MEM-TC-DOM-001, 003~013, 015 및 MEM-TC-DOM-016의 dispatch process subset |
| `apps/memory/tests/application/test_lifecycle.py` | Create/Start/Complete/Close/Delete-pending UoW, same-request replay, matching terminal CompleteTurn replay와 mismatch conflict, clock expiry write 차단, dispatch insert failure rollback, unknown outcome fail-closed | MEM-TC-APP-002, 008~012, 015, 016, 040, 041의 mutation 차단, 043의 tombstone/purge-job 기반 |
| `apps/memory/tests/application/test_dispatch.py` | Claim/publish/expired-recovery command만 상태를 전이하고 stale fencing을 rollback | MEM-TC-APP-017, 017A, 018 |
| `apps/memory/tests/adapters/test_schema.py`, `test_repository.py` | 15개 model/readiness, nullable reference의 tenant-scoped composite FK와 Access Grant canonical binding FK, lifecycle/turn/entry/dispatch CAS와 terminal/fencing check, projection별 암호화 envelope, safe DB error 변환 | MEM-TC-DB-001, 004, 007, 008, 010, 023의 schema/repository subset |
| `apps/memory/tests/adapters/test_disposable_postgres.py` | 실제 PostgreSQL clean upgrade와 Memory foundation/Public capability replay revision별 Alembic model drift·round-trip check, Access Grant binding mismatch DB rejection, legacy Run/NodeRun 보존 downgrade, concurrent StartTurn 단일 승자, StartTurn partial-write rollback, dispatch claim/publish | MEM-TC-DB-002, 007, 013, 014, 017 및 MEM-TC-MIG-005 |

Disposable PostgreSQL evidence는 `NODEASE_RUN_DISPOSABLE_DB_TEST=1`인 전용 CI job에서 한 번 실행하고 일반 `memory-tests` job에서는 제외한다. MEM-TC-DOM-016의 active Turn safe terminal 처리, MEM-TC-DB-003/005~007/009/011~012/015~026과 lifecycle audit/outbox cardinality는 관련 application adapter가 구현되기 전 완료로 표시하지 않는다.

| Requirement range | Primary test sections |
| --- | --- |
| MEM-REQ-001~007 | Architecture Boundary, Session Aggregate, Lifecycle, Gateway/API |
| MEM-REQ-010~019 | Session Aggregate, Start/Complete Turn, Dispatch, PostgreSQL, E2E concurrency |
| MEM-REQ-020~026 | Node Configuration, Workflow Runtime, Client, Migration |
| MEM-REQ-030~039 | Authorization/Provenance, Runtime Provenance, Context Lease, Security |
| MEM-REQ-040~049 | Public history Gateway/API/Client, authenticated internal boundary E2E, Security |
| MEM-REQ-050~059 | BuildMemoryContext, Context Lease, Summary Process, Summarizer/Budget, Observability |
| MEM-REQ-060~069 | Lifecycle, Client Transcript, Purge Receipt, Privacy/Security, Observability |
| MEM-REQ-070~079 | Rolling Deployment And Migration, Dispatch Processing, Workflow Runtime, Legacy E2E |
| MEM-REQ-080~085 | Context Materialization, Provider Attempt Reliability, Summary Pricing, Security/E2E |
| MEM-REQ-086~096 | Version Binding, Principal/Capability, Purge, Audit Cardinality, Cross-Domain E2E |
| MEM-NFR-001~009 | Architecture Boundary, API cache/idempotency/CSP, Performance/Reliability, Property/Fuzz |

## MBA-318 Boundary Completion Regression Matrix

- MEM-TC-BOUND-001: multi-LLM graph에서 지정 consumer만 history를 provider prompt와 RAG query에 사용하고 classifier/다른 provider node는 history를 받지 않는다.
- MEM-TC-BOUND-002: missing/not-found/non-LLM/unknown-version consumer mapping은 preflight, run과 Worker에서 fail-closed한다.
- MEM-TC-BOUND-003: broker가 forged consumer ref를 보내도 Worker는 deployment snapshot mapping으로 덮어쓴다.
- MEM-TC-BOUND-004: Public RAG retrieve, collection retrieve와 policy block audit은 모두 `actor_id=null`, `actor_type=public`이다.
- MEM-TC-BOUND-005: tokenizer model lookup과 exact fallback이 모두 실패하면 provider 호출 없이 `conversation.token_count_unavailable`다.
- MEM-TC-BOUND-006: legacy control 요청은 budget, secret migration, DB mutation과 task publish를 한 번도 호출하지 않는다.
- MEM-TC-BOUND-007: 새 Frontend+구 Gateway는 capability 필드 누락을 `memory_mode`/`conversation_id` 없는 legacy root로 처리한다.
- MEM-TC-BOUND-008: 구 Frontend+새 compatibility Gateway는 root 요청이 성공하되 memory/conversation control을 제거하고 content persistence를 억제한다.
- MEM-TC-BOUND-009: 새 Frontend+새 Gateway는 `client_history_v1`에서 `/chat`을 사용한다.
- MEM-TC-BOUND-010: strict Gateway는 root public Chatbot을 history-required로 거부한다.
- MEM-TC-BOUND-011: Gateway dispatch는 raw history 대신 600초 TTL 일회성 Redis reference, Celery `expires`와 absolute deadline을 전달한다.
- MEM-TC-BOUND-012: deadline이 지난 broker payload는 Session/Knowledge/Engine/provider 접근 전에 non-retryable하게 종료한다.
- MEM-TC-BOUND-013: Worker는 malformed 또는 timezone 없는 public deadline을 fail-closed한다.
- MEM-TC-BOUND-014: compatibility와 strict 모두 Public WorkflowRun/NodeRun/Trace 원문 저장을 활성화하지 않는다.
- MEM-TC-BOUND-015: Worker는 raw history만 있거나 opaque reference와 raw history가 함께 있는 queued task를 DB/Redis/Engine 전에 non-retryable하게 거부한다.
- MEM-TC-BOUND-016: Redis store unavailable은 consume 전 bounded retry를 사용하지만 invalid/missing/corrupt 또는 응답 유실 뒤 이미 소비된 reference는 provider replay 없이 종료한다.
- MEM-TC-BOUND-017: Public root와 `/chat`은 조회한 deployment version에 결박되고 mismatch는 transient store/task publish 전 safe 409다.
- MEM-TC-BOUND-018: 열린 Embed Chat이 `legacy_v0`과 `client_history_v1` 사이에서 재배포되면 info를 no-store로 갱신하고 이전 history를 폐기한 뒤 새 version으로 한 번만 재시도한다.
- MEM-TC-BOUND-019: current inputs canonical JSON byte 상한과 Public `/chat` HTTP body 상한은 각각 tokenizer와 JSON parsing 전에 거부하며 error body는 원문을 반사하지 않는다.
- MEM-TC-BOUND-020: sanitizer가 한 message를 빈 값으로 만들면 완료 pair 전체를 제거하고, 정제로 늘어난 internal marker에는 raw message/envelope 상한을 다시 적용하지 않되 final framed projection token 상한을 지킨다.
- MEM-TC-BOUND-021: Client는 Unicode scalar/message 및 UTF-8 envelope 상한을 넘는 완료 pair를 history에서 제외·oldest-pair 단위 축소하며 oversized 성공 응답은 화면에 유지하고 이후 정상 turn을 막지 않는다.
- MEM-TC-BOUND-022: Public Gateway는 versioned task/전용 queue에 publish하고 새 Worker entrypoint만 그 queue를 함께 소비한다. 일반 task에 잘못 전달된 public context는 DB·Redis·Engine 전에 fail-closed한다.
- MEM-TC-BOUND-023: 유효한 empty history라도 `/chat`에 `deployment_version`이 없으면 실행 service 호출 전에 safe 422로 거부하고 root compatibility route는 기존 optional version 계약을 유지한다.
- MEM-TC-BOUND-024: 서로 다른 두 embedding model group에서 첫 provider 호출 뒤 deadline이 만료되면 두 번째 provider는 호출하지 않으며 `safe_no_result`가 deadline 예외를 삼키지 않는다.
- MEM-TC-BOUND-025: Redis `SET`이 응답하지 않으면 async transient store가 bounded timeout 안에 `conversation.history_store_unavailable`로 종료한다.
- MEM-TC-BOUND-026: Compose와 Helm values→ConfigMap→Gateway env가 `PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE`를 전달한다.
- MEM-TC-BOUND-027: Nginx Public `/chat` read/send timeout이 600초 absolute deadline보다 길다.
- MEM-TC-BOUND-028: Nginx가 oversized Public `/chat`을 차단해도 safe JSON code와 no-store/no-referrer headers를 반환한다.
- MEM-TC-BOUND-029: Helm default/production values와 Ingress template은 600초보다 긴 Public read/send timeout을 렌더링하고 operator annotations를 보존한다.
- MEM-TC-BOUND-030: 동일한 deployed judge-first routing은 일반 실행에서 learning label을 queue하지만 `suppress_content_persistence` 실행에서는 queue하지 않고 safe suppression status만 남긴다.
- MEM-TC-BOUND-031: Redis history consume은 short-lived client에 2초와 남은 task deadline 중 더 짧은 connect/read timeout을 적용하고 client를 닫는다.
- MEM-TC-BOUND-032: runtime Judge의 최초 provider가 incomplete를 반환한 뒤 deadline guard가 만료되면 compact retry provider를 호출하지 않고 deadline 예외를 전파한다.
- MEM-TC-BOUND-033: 전용 Public task의 `memory_mode=true` 또는 non-null `conversation_id`는 DB 전에 거부되며 safe false/null sentinel은 canonical engine context에서 제거된다.
- MEM-TC-BOUND-034: ASGI middleware가 Public root와 `/chat` 요청의 body buffering 전에 생성한 동일 deadline이 Gateway service, execution context와 Celery `expires`까지 보존된다.
- MEM-TC-BOUND-035: admission 전에 만료된 Public 요청은 budget/secret migration/Redis/task publish를 호출하지 않고 safe 504로 종료하며, Redis TTL·I/O timeout과 Gateway result polling은 고정 600초가 아니라 남은 deadline을 사용한다.
