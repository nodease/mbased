# ADR-0030: Memory Bounded Context

Status: Accepted
Related ADRs: [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0009](ADR-0009-active-organization-header-context.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md), [ADR-0020](ADR-0020-knowledge-mcp-incremental-sync-boundary.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0023](ADR-0023-audit-actor-access-management-boundary.md)

## Context

현재 기억 기능은 Client의 캔버스 전역 `memory_mode`, Gateway의 실행 context 변환, Chatbot 배포의 강제 활성화, Workflow Engine LLM node의 실행 로그 재조회와 요약으로 나뉘어 있다. `WorkflowRun`과 `WorkflowNodeRun`은 Log System이 비동기로 기록하므로 직전 turn의 read-your-writes를 보장하지 않으며, 대화 lifecycle과 audit/trace retention이 결합되어 있다.

현재 실행 로그에는 최종 답변에 기여한 Knowledge, connector, tool, subworkflow source의 일반화된 provenance가 없다. 따라서 private Knowledge 권한이 회수되거나 source가 삭제된 뒤에도 과거 답변이 기억을 통해 다시 provider prompt로 전달될 수 있다. Multi-LLM workflow에서는 모든 LLM node가 같은 전역 flag를 사용해 역할이 다른 node의 이력이 섞이고 summary 호출 수와 비용도 node 수만큼 증가할 수 있다.

Gateway는 공개·인증 conversation 진입점과 lifecycle을 처리하고 Workflow Engine은 turn 결과를 생성한다. Memory를 어느 한 runtime의 helper로 두거나 두 runtime이 같은 table을 직접 변경하면 reset/delete와 늦은 Worker completion, Celery retry, 동시 요청 사이의 mutation ownership이 불명확해진다.

## Decision

Memory를 Workflow, Chatbot 또는 Log System의 부가기능이 아닌 독립 bounded context로 둔다.

Memory bounded context는 다음 lifecycle의 유일한 업무 mutation owner다.

- `ConversationSession`
- public session의 `ConversationAccessGrant`
- `ConversationTurn`
- `ConversationMemoryEntry`
- `ConversationMemorySummary`
- `MemoryDataDependency`
- `TurnDispatchJob`
- `ConversationPurgeJob`
- summary generation과 Memory context authorization lease
- conversation retention, close, reset, delete와 purge

Gateway, Workflow Engine, Log System과 다른 도메인은 Memory table을 직접 변경하지 않는다. Memory application command/query 또는 domain event로만 협업한다.

### Deployment Form

독립 도메인과 독립 네트워크 서비스는 같은 결정이 아니다. 초기 구현은 ADR-0022의 헥사고널 경계를 따르는 in-process 모듈러 모놀리스 package로 시작한다.

```text
apps/memory/
  domain/
  application/
  adapters/

apps/gateway/composition/memory.py
apps/workflow_engine/composition/memory.py
```

빈 package를 먼저 만들지 않고 첫 구현 use case와 함께 최소 구조를 추가한다. Domain/application은 FastAPI, Celery, SQLAlchemy model, provider SDK와 Gateway/Workflow concrete 구현을 import하지 않는다. Runtime별 outer composition root가 concrete adapter와 use case를 조립한다.

현재 Alembic metadata와 공통 ORM registry를 유지해야 하면 persistence model은 `apps/shared/db/models/`에 둘 수 있다. 이 물리 위치가 업무 소유권을 의미하지 않으며 Memory persistence adapter 외 production code의 직접 query/mutation을 새로 허용하지 않는다.

독립 scaling, 장애 격리, data residency 또는 별도 배포 주기가 필요해질 때 application contract를 유지하고 transport adapter를 교체하는 별도 서비스 분리를 후속 ADR로 결정한다.

### Responsibility Boundaries

Memory는 다음 정책을 소유하지 않는다.

- 사용자 인증, organization membership과 RBAC
- Knowledge, connector, tool과 subworkflow resource의 current authorization
- Workflow graph 실행과 node scheduling
- Deployment audience와 active surface 정책
- LLM credential, provider allowlist와 조직 전체 budget
- 공통 PII/secret 탐지와 masking 알고리즘
- 원본 Knowledge 문서, raw tool payload, Workflow execution log와 AuditLog

Memory는 각 source-owning domain이 제공하는 canonical identity와 capability port를 사용한다. 다른 도메인의 권한·비용·배포 규칙을 Memory 내부 SQL이나 enum으로 복제하지 않는다. Privacy classification/redaction은 shared privacy boundary가 제공하고 Memory는 해당 결정을 저장·전파하는 consumer다.

Memory 경계에서 사용하는 principal은 다음처럼 분리한다.

| Principal | 의미 | Memory에서의 사용 |
| --- | --- | --- |
| Execution subject | 현재 요청의 인증 사용자 또는 향후 승인된 service account | Authenticated session binding과 source authorization 재평가 |
| Anonymous public audience | 사용자 identity나 subject가 없는 public 실행 audience | Public session binding과 public-only source authorization 재평가 |
| Credential principal | LLM credential 사용을 서버가 승인한 service/deployment 주체 | provider execution capability 발급 근거 |
| Billing principal | 비용이 귀속되는 organization/workflow/deployment | budget reservation과 usage 귀속 |
| Audit actor | 관리·보안 lifecycle을 실제로 요청한 인증 사용자. Public 요청은 `actor_id=null`, `actor_type='public'` | AuditLog actor |
| Conversation Access Grant | 특정 public session을 이어가기 위한 bearer capability | session 접근 검증만 담당하며 identity나 위 principal을 대신하지 않음 |

App/deployment 생성자는 deployment policy가 서버에서 credential 또는 billing principal로 명시적으로 파생할 때만 해당 역할을 가질 수 있다. 생성자를 execution subject나 public audit actor로 대체하지 않는다.

### Conversation Aggregate And Concurrency

`ConversationSession`, `ConversationTurn`, `ConversationAccessGrant`, `TurnDispatchJob`, `ConversationPurgeJob`, `SummaryGenerationJob`, `MemoryContextLease`, `MemoryContextProviderAttempt`는 각각 bounded aggregate root다. Turn, grant, dispatch, purge, summary process, lease와 context provider attempt를 Session object graph 안에 적재하지 않고 opaque ID와 revision으로 연결한다. At-least-once delivery에서 idempotent mutation을 보장하되 하나의 revision으로 모든 변경을 직렬화하지 않는다.

Session의 single-active-turn claim, 새 Turn과 Dispatch Job/outbox 생성은 유실 없는 admission을 위한 명시적 cross-aggregate consistency boundary로 같은 UnitOfWork에 둔다. `CompleteTurn`의 Turn terminal 전이 + Session active-turn 해제/content revision 증가 + final entry/projection 승격 + required outbox도 read-your-writes와 부분 완료 방지를 위해 하나의 UnitOfWork로 commit한다. Reset의 old-session close + new-session/grant 생성과 Delete의 tombstone + grant revoke + purge job/outbox도 접근 차단 유실을 막기 위한 lifecycle UnitOfWork다. 이는 Memory의 다른 aggregate를 임의로 한 transaction에서 변경하는 일반 규칙이 아니다. Summary process는 각 root의 version/CAS와 application process로 조정한다.

- `lifecycle_revision`: close, reset, delete와 같은 session lifecycle
- `content_revision`: completed turn과 approved projection
- `turn_version`: pending turn의 start/complete 전이
- `source_revision`: summary가 사용하는 content snapshot

`StartTurn`은 transport idempotency key에서 정규화한 canonical request ID, bounded request fingerprint, expected lifecycle revision과 active-turn constraint를 검증한다. `CompleteTurn`은 turn version과 start 당시 lifecycle revision을 검증한다. Summary나 unrelated projection 변경은 정상 turn completion을 stale로 만들지 않는다. Close/reset/delete 이후 도착한 Worker 결과는 session을 되살릴 수 없다.

Failed 또는 cancelled turn은 lifecycle 또는 transcript 정책에 따라 보존할 수 있지만 기본 Memory Context 후보에서는 제외한다.

Session은 생성 시 canonical organization/app/workflow와 함께 `deployment_id`, immutable deployment version 또는 snapshot hash, conversation mapping version, node Memory policy version, `memory_contract_version`, `storage_generation`에 고정한다. Runtime은 app의 현재 active deployment를 다시 조회해 기존 session을 자동 재결합하지 않는다. Active deployment version이 바뀌면 기존 session은 자동 migration하지 않고 새 session을 요구한다. Public surface는 기존 session 정보를 숨기고 새 conversation 생성 흐름을 제공하며, authenticated surface는 typed conflict와 safe new-session action을 제공한다.

### Turn Dispatch And Worker Compatibility

`StartTurn`의 pending turn 생성과 `TurnDispatchJob` outbox 저장은 같은 DB transaction에서 원자적으로 commit한다. Gateway는 transaction commit 뒤 Celery publish 성공을 실행 접수의 source of truth로 간주하지 않는다. Dispatcher가 durable job을 claim하고 publish하며 publish 실패, process crash와 ambiguous broker acknowledgement를 재처리한다.

Turn은 `pending_dispatch`, `queued`, `running`, `completed`, `failed`, `cancelled` 상태를 가진다. Memory-owned pending dispatch claim과 Workflow-owned running execution lease는 각각 bounded deadline을 가진다. Durable Workflow admission 전에는 같은 dispatch ID를 안전하게 재발행할 수 있지만 admission 이후 outcome이 불명확하면 Memory는 arbitrary workflow를 재실행하지 않고 Workflow domain의 execution reconciliation 결과를 기다리거나 turn을 safe terminal failure로 닫는다. 임의 tool/connector와 외부 node side effect의 재개·재시도 가능 여부는 Workflow domain의 idempotency 계약을 따른다.

Memory task envelope은 `memory_contract_version`, `storage_generation`, minimum worker capability와 opaque session/turn/dispatch handle을 포함한다. Worker는 외부 side effect 전에 capability를 검증한다. Rolling migration 동안 target Memory task는 capability가 확인된 versioned queue 또는 전용 worker pool만 소비한다. Deployment preflight는 보조 방어선이며 mixed worker pool에서 개별 task 검증을 대체하지 않는다.

Dispatch 상태는 adapter가 ORM을 직접 변경하지 않고 Memory application command로만 전이한다. Workflow domain은 durable `AdmitExecution(dispatch_id)`으로 중복 execution admission을 제거하고, Memory는 admission reference를 관찰해 queued/running 상태를 갱신한다. Admission 성공 후 Memory acknowledgement가 유실되면 reconciler가 dispatch ID로 Workflow admission을 조회해 복구한다. Workflow execution lease/heartbeat는 Workflow domain이 소유하며 Memory가 복제하지 않는다.

### Storage And Legacy Data

Conversation Memory는 Workflow execution log와 분리된 dedicated store를 source of truth로 사용한다. Log System은 observer이며 Memory write 성공을 대신하지 않는다.

Legacy `WorkflowRun`/`WorkflowNodeRun`에는 current authorization을 재검증할 Data Dependency가 없다. 따라서 provenance 없는 legacy history는 기본 Memory Context에 포함하지 않는다. Compatibility는 기존 workflow 실행 수용을 뜻하며 unsafe history 재사용을 보장하지 않는다. Deployment와 session은 `memory_contract_version`과 `storage_generation`에 고정하고 요청마다 legacy/new reader 또는 writer를 fallback하지 않는다.

### Provenance And Current Authorization

Final answer 문자열에서 source를 사후 추론하지 않는다. Knowledge retrieval, connector/tool, subworkflow와 policy adapter가 server-derived bounded `DataDependencyRef`를 `RuntimeDataDependencyEnvelope`로 node 결과와 함께 반환하고 Workflow Runtime이 dependency 합집합을 최종 output까지 전파한다. V1에서는 output 내용에 영향을 준 dependency를 모두 필수로 취급하며 optional dependency 의미를 지원하지 않는다. Server가 외부/private source 영향이 없음을 확인해 발급한 explicit complete empty envelope은 허용하지만, envelope 누락이나 completeness unknown을 empty와 동일하게 취급하지 않는다.

Memory Entry와 Summary는 source kind, organization, canonical resource/version, sensitivity, authorization-safe reference와 decision revision을 보존한다. Client 또는 arbitrary node output은 canonical dependency를 선언할 수 없다. Provenance를 보존하지 못한 private/sensitive derived output은 Memory write에서 fail-closed 한다.

Memory read는 current execution subject/audience와 source-owning authorization port를 다시 평가한다. Authorization 응답은 최소 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 제공한다. Source ACL이 있는 resource는 ACL revision도 결정 revision에 반영해야 한다. 익명 public audience는 subject가 아니라 `anonymous_public_audience` principal kind로 평가한다. Source-owning adapter는 membership/team/direct permission/public visibility/source ACL/lifecycle처럼 결정에 영향을 주는 값이 바뀌면 decision revision을 변경한다. 필요한 revision이 없거나 검증 불가능한 응답은 fail-closed 한다. Dependency 하나라도 거부되거나 검증 불가능하면 entry 전체를 제외한다. Summary는 source entry dependency의 합집합과 lifecycle을 상속하며 독립적인 권한 source가 아니다.

Source별 provenance 생산 책임은 다음과 같다.

| Source | Canonical dependency 생산자 | 전파 규칙 |
| --- | --- | --- |
| Knowledge retrieval | Knowledge Permission Helper/retrieval adapter | KB와 document/version, sensitivity, authorization-safe reference를 발급 |
| Connector/tool | 승인된 connector/tool adapter | connector resource/item revision, source ACL/egress policy revision을 발급 |
| Subworkflow | Workflow Runtime | target app/deployment version과 child output envelope 합집합을 반환 |
| LLM node | Workflow Runtime/provider adapter | prompt input, Memory Context, retrieval, tool 결과 envelope의 합집합을 output에 상속 |
| Transform/code node | Workflow Runtime | 모든 content input envelope의 합집합을 그대로 상속하며 canonical dependency를 새로 만들거나 제거하지 않음 |
| System/privacy policy | 해당 policy owner adapter | classification/redaction policy revision을 발급 |

Code/custom node 결과가 provenance를 반환하지 못하면 public-only이며 비민감하다는 server-side 증명이 없는 한 private/sensitive Memory write를 거부한다.

LLM Credential/egress 경계는 `BuildMemoryContext` 전에 `purpose=main_generation`인 `ProviderExecutionCapability`를 발급한다. `BuildMemoryContext`는 session, execution subject/audience, node, capability identity/revision과 authorization decision revision에 binding된 short-lived context lease와 context handle을 반환한다. Context handle은 raw text 복사본이 아니라 ordered entry/summary reference, policy version, server-keyed content digest와 version으로 구성된 short-lived materialization plan을 가리킨다. Raw Memory Context는 provider adapter가 main LLM 호출 직전에 유효한 lease를 같은 `ProviderExecutionCapability`와 provider attempt ID로 claim하고 current source authorization을 재검증한 뒤 Memory store에서 materialize할 때만 획득한다.

Lease claim은 같은 provider attempt에 idempotent하다. Provider adapter는 outbound 호출 직전에 `provider_started`를 durable하게 기록한다. Claim 뒤 `provider_started` 전 crash는 claim expiry 후 새 lease/attempt로 재승인할 수 있지만, `provider_started` 이후 outcome이 unknown이면 provider를 자동 재호출하지 않고 reconciliation 또는 safe node failure로 닫는다. Permission/source invalidation event는 관련 entry, summary, materialization plan과 미사용/claimed-not-started lease를 무효화할 수 있지만 read-time current authorization을 대체하지 않는다. 외부 provider 호출과 permission mutation의 완전한 원자성은 보장하지 않으며 lease로 stale authorization window를 제한한다.

Summary generation lease도 source authorization decision revision set에 binding하고 summary provider 호출 직전에 검증한다. Invalid/revoked/unknown lease 상태에서는 raw history를 summarizer로 전송하지 않는다.

### Public And Authenticated Sessions

Public conversation token은 사용자 identity를 인증하지 않지만 특정 public conversation을 이어갈 권한을 주는 bearer capability credential이다. Secret으로 취급하고 Access Grant source-of-truth에는 verifier hash만 저장한다. 응답 유실 복구가 필요한 경우 별도 idempotency response store에 application-encrypted token을 최대 10분 보관할 수 있으며 TTL 뒤 복구 불가능하게 삭제한다. Secret replay TTL 뒤 같은 idempotency key를 다시 사용하면 새 grant를 만들거나 rotation하지 않고 typed `memory.secret_replay_expired` conflict를 반환한다. 새 conversation은 새 idempotency key로 명시적으로 생성하며 접근 불가능해진 기존 session/grant는 idle expiry와 retention cleanup 대상이다. Access Grant는 session, deployment ID/version과 audience binding, expiry, rotation과 revoke lifecycle을 가진다. Token 원문을 URL, audit, trace, metric label과 application log에 남기지 않는다.

Public session을 로그인 후 authenticated session으로 자동 승격하거나 병합하지 않는다. 로그아웃 후 authenticated session을 public endpoint에서 이어가지 않는다. Public bearer capability는 private Memory 접근 근거가 될 수 없다. Anonymous RAG는 ADR-0018의 public-only 경계를 계속 따른다.

Public endpoint는 cookie credential을 사용하지 않고 Conversation authorization header만 수용한다. Public session 생성·실행은 deployment, grant, network source와 조직 비용 scope에 bounded rate/concurrency/turn/cost limit을 적용한다. Raw grant 발급·rotation 응답은 idempotent response recovery를 지원해야 하며 응답 유실 때문에 동일 logical request가 여러 active grant를 만들 수 없다.

Authenticated cookie endpoint의 mutation은 CSRF token, exact allowed Origin과 Fetch Metadata를 검증한다. CORS 허용은 공개 credential-less surface와 인증 surface를 분리한다. Token, transcript와 lifecycle response는 shared cache에 저장하지 않는다.

`public_chatbot`과 `authenticated_internal_chatbot`은 같은 시각 Chatbot component를 재사용할 수 있지만 backend runtime surface, route, authentication, CORS/Origin, deployment access policy와 session namespace를 분리한다. Public surface는 ADR-0018대로 public Knowledge만 허용한다. Private Knowledge를 사용하는 authenticated internal surface는 별도 내부 Chatbot 접근 정책과 배포 계약이 구현된 뒤에만 활성화할 수 있으며, public Access Grant를 내부 실행 권한으로 승격하지 않는다. Exact Origin/embed allowlist는 deployment-owned versioned configuration이며 client 입력이나 Memory가 완화하지 않는다.

Public purge job은 발급 후 7일 안에 `completed`, durable runtime isolation을 마친 `completed_with_hold`, 또는 `terminal_failure`로 전이한다. Retryable failure가 7일을 넘으면 terminal failure와 운영 dead-letter/alert로 승격한다. Purge receipt는 최소 terminal 시점 후 24시간까지 유효하고 최대 발급 후 8일까지 유지해 public caller가 terminal 상태를 확인할 수 있게 한다.

### Summary And Cost Consistency

Memory summary는 일반 LLM 호출과 같은 approved model/credential, data egress, budget, timeout과 usage 정책을 적용한다. Memory adapter가 model/credential 정책을 독자적으로 결정하지 않고 LLM Credential/egress domain이 승인한 `ProviderExecutionCapability`만 실행한다. 초기 `summaryModelPolicy`는 `inherit_node`만 지원하며 별도 조직 기본 model/credential preset ADR과 구현 전에는 `organization_default`를 제공하지 않는다.

`ProviderExecutionCapability`는 organization, workflow, deployment ID/version, node/invocation, provider/model/credential safe reference, `purpose=main_generation|memory_summary`, egress policy revision, pricing revision, 최대 token/cost bound와 expiry에 binding된 server-issued opaque capability다. Main generation과 summary provider call, Memory context lease claim, budget reservation과 usage reconciliation은 같은 capability identity와 revision을 검증해야 한다. Access Grant나 client가 provider/model/credential을 선택하거나 capability scope를 확장할 수 없다.

Summary 생성은 DB transaction 하나로 provider 호출까지 묶지 않는다. Durable generation job과 idempotent 상태 전이로 generation lease, capability-bound budget reservation, provider result, summary compare-and-swap, usage commit과 reconciliation을 조정한다. Reservation은 provider/model/pricing revision, purpose, token/cost cap과 capability expiry에 고정한다. Generation lease는 재획득마다 증가하는 fencing generation을 가지며 summary CAS는 현재 generation만 허용한다. Stale generation의 늦은 provider 결과는 summary로 채택하지 않지만 실제 provider attempt usage는 attempt idempotency key로 정확히 한 번 reconcile한다. Provider 결과가 불명확하거나 summary 저장 후 usage commit이 실패해도 provider를 무조건 다시 호출하지 않는다. Summary는 usage/reconciliation terminal 상태 전에는 새 Memory Context에서 재사용하지 않는다.

기존 Workflow Budget의 사후 집계·차단 계약은 일반 실행에 대한 예약을 제공하지 않는다. Memory summary는 별도 원자적 reservation capability가 구현되기 전에는 `window` 전략만 사용하거나 fail-closed 해야 하며, 예약 없이 `window_then_summary`를 활성화하지 않는다.

Summary model 가격을 산정할 수 없거나 estimate가 invalid/zero-by-unknown이면 reservation을 승인하지 않는다. 이 경우 `budget.price_unavailable`로 summary provider를 호출하지 않고 `window` 또는 configured node failure policy로 닫는다. 임의 0원 예약이나 Memory adapter의 추정 가격 fallback은 허용하지 않는다.

Node memory read/summary failure policy와 user/final assistant turn write failure policy를 분리한다. 최종 turn write 성공 여부를 개별 LLM node 설정이 결정하지 않는다.

### Retention And Purge

Delete는 session/grant 접근을 먼저 차단한 뒤 다음 record class를 정책에 따라 처리한다.

| Record class | Purge contract |
| --- | --- |
| Content-bearing | Turn content, final/provisional Entry와 projection, Summary, dependency의 민감 source reference, raw/materialized context cache/plan, transcript/result와 conversation access-token replay ciphertext를 물리 삭제 또는 승인된 irreversible erasure 처리 |
| Session/access state | Session의 subject/audience binding과 Access Grant verifier/source row를 제거한다. Purge status 확인에 필요한 최소 opaque tombstone만 Purge Job/receipt expiry까지 유지 |
| Operational process | Turn Dispatch Job, Summary Generation Job, Memory Context Lease, Provider Attempt, idempotency/replay record는 content와 민감 reference를 제거하고 bounded operational/accounting retention 동안 opaque state, timestamp, safe reason만 유지한 뒤 삭제 |
| Audit/usage | 각 소유 도메인의 retention을 따르며 raw content, token/hash, prompt, private source identity를 포함하지 않음 |
| Purge control | Purge Job, verifier-hash receipt와 delete 응답 유실 복구용 encrypted receipt replay는 각각 정해진 TTL/receipt expiry까지만 유지한 뒤 삭제. Raw session content 접근에는 사용하지 않음 |

Legal hold content는 runtime/provider/일반 operator가 접근할 수 없는 compliance boundary로 이동하고 public purge status를 `completed_with_hold`로 terminal 처리한다. 이는 물리 삭제 완료가 아니므로 `memory.session.purged`를 기록하지 않는다. Hold 해제는 별도 compliance erasure process가 처리하며 실제 erasure가 완료된 시점에만 `memory.session.purged`를 기록한다. 이미 terminal인 public purge status를 `completed`로 되돌려 쓰거나 receipt를 재발급하지 않는다. `terminal_failure`도 purge 완료를 뜻하지 않으며 운영 alert/recovery 대상으로 남긴다.

`completed`/physical purge는 모든 configured content-bearing live store/cache, conversation access-token replay와 backup/export retention contract가 삭제 또는 승인된 irreversible crypto-erasure를 확인한 경우에만 사용한다. 위 표의 최소 purge-control tombstone, receipt verifier와 encrypted delete-response replay만 상태 조회를 위해 정해진 TTL/receipt expiry까지 남길 수 있으며 raw session content 접근에는 사용할 수 없다. Backup 상태가 unknown이거나 erasure marker를 확인하지 못하면 completed로 낙관하지 않고 retry/terminal failure 또는 compliance isolation으로 닫는다.

### Audit And Privacy

AuditLog canonical action은 관리·보안 의미가 있는 `memory.session.*`와 `memory.grant.*` lifecycle에 제한한다. 정상 `memory.turn.*`와 `memory.summary.*` 상태 전이는 high-cardinality operational domain event/trace/metric이며 기본 AuditLog row를 만들지 않는다. Turn/summary의 permission/policy 차단은 기존 `permission.denied`/`policy.block`, provider 호출은 `llm.call`, workflow 실행은 `workflow.execute`를 재사용해 중복 감사 action을 만들지 않는다. Organization-scoped audit event는 관리자 조회가 적용할 수 있도록 safe `organization_id`를 필수 metadata로 기록한다. Raw Memory content, token/hash, prompt, private source identity와 provider raw error는 audit/trace에 저장하지 않는다. Security/lifecycle mutation과 필수 audit/outbox 기록은 같은 transaction 또는 durable outbox로 묶는다.

Lifecycle operation별 canonical audit cardinality는 다음과 같다.

| Operation | Required canonical actions |
| --- | --- |
| Authenticated create | `memory.session.created` 1건 |
| Public create | `memory.session.created` 1건 + `memory.grant.issued` 1건 |
| Close | `memory.session.closed` 1건. Transcript-only 여부는 closed session 정책에서 파생하며 grant rotation action을 만들지 않음 |
| Reset | old `memory.session.reset` 1건 + old `memory.grant.revoked` 최대 1건 + new `memory.session.created` 1건 + public이면 new `memory.grant.issued` 1건. 별도 `closed` 중복 기록 금지 |
| Delete request | `memory.session.delete_requested` 1건 + active public grant가 있으면 `memory.grant.revoked` 1건 |
| Physical purge complete | `memory.session.purged` 1건, `actor_id=null`, `actor_type='system'` |
| `completed_with_hold` / `terminal_failure` | Operational/compliance status와 alert만 기록하고 `memory.session.purged` 금지 |

요청 기반 authenticated lifecycle action의 audit actor는 실제 요청 사용자다. Public create/close/reset/delete request와 grant action은 `actor_id=null`, `actor_type='public'`을 사용하며 app/deployment owner를 가짜 actor로 기록하지 않는다. 비동기 physical purge/compliance erasure completion은 실제 실행 주체인 `actor_id=null`, `actor_type='system'`을 사용하고 원 delete request와 safe purge request reference로 연결한다.

### Memory Types

초기 구현 범위는 Workflow/Chatbot Conversation Memory와 node별 Memory policy다. Agent Builder authoring session memory와 Personal Agent user-global memory는 lifecycle, opt-in, ownership과 view/edit/delete 정책이 다르므로 같은 scope나 table에 자동 통합하지 않는다. Personal Agent memory는 별도 제품 요구사항과 후속 결정 전까지 구현하지 않는다.

## Consequences

장점:

- Gateway와 Worker의 session mutation ownership이 명확해진다.
- read-your-writes, retry idempotency, reset/delete race를 aggregate에서 검증할 수 있다.
- 권한 회수와 source deletion 후 Memory가 우회 데이터 경로가 되는 것을 막는다.
- Workflow execution log, Audit/Trace와 conversation retention을 분리할 수 있다.
- Agent/Personal Memory를 Workflow LLM node 구현에 종속시키지 않고 확장할 수 있다.

비용:

- dedicated schema, migration, UnitOfWork, authorization adapter와 reconciliation worker가 필요하다.
- Workflow Runtime 전체에 bounded provenance envelope를 전파해야 한다.
- 기존 global memory mode와 active chatbot을 guided migration해야 한다.
- Gateway와 Workflow Engine 양쪽의 composition/import boundary test가 필요하다.
- 초기에는 legacy와 target 문서·compatibility path가 함께 존재한다.

## Implementation Status

이 ADR은 목표 경계와 신규 코드 작성 기준을 승인한다. 현재 실행 로그 기반 기억 구현이 이 구조로 이관됐다는 뜻은 아니다. 구체 API, data model, migration과 테스트 계약은 [Conversation Memory feature 문서](../features/conversation-memory/requirements.md)를 따른다.

## Alternatives Considered

- **Gateway가 turn row 저장 후 Celery를 직접 publish**: 구현은 단순하지만 commit 이후 crash/publish ambiguity로 active turn이 영구 정체될 수 있어 거부했다.
- **Memory가 Workflow task 전체를 자동 재실행**: 외부 tool/connector side effect의 idempotency를 Memory가 알 수 없어 거부했다. Memory는 dispatch/admission과 자기 소유 write만 중복 방지한다.
- **Preflight만으로 Worker 호환성 보장**: mixed rolling pool에서 실제 consumer를 보장하지 못해 거부했다. Versioned routing과 runtime capability guard를 함께 사용한다.
- **Public token을 HttpOnly authentication cookie로 통합**: public embed와 authenticated session의 권한 의미가 섞이고 CSRF/CORS 계약이 복잡해져 거부했다. Public grant header와 authenticated cookie surface를 분리한다.
- **Build 단계에서 raw context 반환 후 lease만 검증**: caller가 이미 context를 확보해 revoke 창을 줄이지 못하므로 거부했다. Provider adapter의 lease claim/materialization 시점에 raw context를 획득한다.
- **Lease claim만 기록하고 provider attempt 상태를 두지 않음**: claim 직후 crash와 unknown provider outcome을 구분할 수 없어 거부했다. Materialization plan, provider attempt와 `provider_started` marker를 사용한다.
- **Budget reservation 없이 summary 허용**: concurrent cache miss와 retry에서 비용 일관성을 보장하지 못해 거부했다. Capability가 없으면 window-only로 제한한다.
- **모든 turn/summary 상태를 AuditLog에 저장**: `workflow.execute`, `llm.call`과 중복되고 public traffic으로 audit storage가 증폭되므로 거부했다. 정상 상태는 operational trace/metric으로 분리한다.

## Affected Documentation

- `docs/architecture.md`
- `docs/features/conversation-memory/`
- `docs/features/chatbot-deployment/`
- `docs/features/deployment/requirements.md`
- `docs/features/auth/`
- `docs/features/organization/`
- `docs/features/llm-credentials/`
- `docs/features/budget-management/`
- `docs/features/audit-tracing/`
- `docs/features/knowledge/`
- `docs/features/connectors/`
- `docs/features/workflow/`
- `docs/glossary.md`
- `docs/data_model.md`

## Follow-Up Review Notes

- 첫 구현 PR은 persistence schema를 확정하기 전에 aggregate/UoW와 outbox transaction test를 작성한다.
- Queue/version migration은 실제 Celery routing과 Worker rollout 절차를 검증하는 별도 구현 단위로 진행한다.
- Public rate/cost 기본값은 부하·비용 측정 후 더 엄격하게 조정할 수 있으며 무제한으로 완화하지 않는다.
- Context lease는 stale window를 줄이지만 permission mutation과 외부 provider 사이의 distributed transaction을 보장하지 않는다는 점을 보안 검토에서 유지한다.
- ADR-0030 목표 전환이 완료될 때 chatbot/deployment의 Legacy Current Implementation 항목과 관련 회귀 테스트를 제거한다.
- Public lifecycle cutover 전에 Audit actor schema/validator/sanitizer/UI가 `actor_type='public'`을 지원하는지 검증한다. 지원 전 app owner 또는 `system` actor로 대체하지 않는다.
