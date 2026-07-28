# Conversation Memory Requirements

Status: Draft
Related Features: workflow, chatbot-deployment, deployment, knowledge, connectors, organization, llm-credentials, budget-management, audit-tracing

## Purpose

Public Chatbot은 서버에 익명 transcript를 영구 저장하지 않고 Client가 매 요청에 전달한 bounded history를 현재 LLM 대화 맥락으로 사용한다. 인증된 조직 내부 Chatbot은 별도 후속 범위에서 독립 Memory bounded context가 session·turn·entry·summary를 안전하게 저장·조회·요약한다.

이 문서는 [ADR-0030](../../decisions/ADR-0030-memory-bounded-context.md), [ADR-0033](../../decisions/ADR-0033-conversation-memory-contract-completion.md)과 Public 결정을 대체하는 [ADR-0074](../../decisions/ADR-0074-public-chatbot-client-held-history.md)를 구체화한다. 아래 durable Session/Turn/Entry 요구사항은 authenticated internal Chatbot 후속 target에 적용하며 Public Chatbot에는 적용하지 않는다.

## Scope

포함:

- Public Chatbot client-held completed user/assistant history
- authenticated internal Chatbot Conversation Session lifecycle
- user/final assistant turn과 명시적 node projection
- LLM node별 Memory read/write policy
- dedicated Memory store와 read-your-writes
- provenance, current authorization와 context lease
- Public stateless request와 authenticated durable session 격리
- summary usage/budget/reconciliation
- retention, close/reset/delete/purge
- safe transcript, audit와 observability
- legacy graph/chatbot migration

제외:

- Personal Agent user-global memory
- organization shared semantic memory
- cross-workflow vector memory search
- Agent Builder authoring clarification memory
- arbitrary code/tool state snapshot과 transaction recovery
- 공개 session과 authenticated session의 자동 병합

## Actors

- Workflow Builder: conversational mapping과 node Memory policy를 구성한다.
- Public Chatbot Visitor: 자신의 브라우저가 보관한 완료 대화 이력을 요청마다 전달한다.
- Authenticated User: current organization/resource 권한으로 내부 conversation을 실행한다.
- Workflow Runtime: turn lifecycle과 Memory Context use case를 호출한다.
- Organization Manager: organization membership과 governance policy에 따라 retention 및 운영 상태를 관리한다.
- Workflow Operator: 자신에게 부여된 workflow/deployment 권한 범위에서 session 운영 상태를 확인하되 organization governance policy를 변경하지 않는다.
- Auditor: raw Memory content 없이 lifecycle·정책 결정의 safe metadata를 확인한다.

## Functional Requirements

### Domain Ownership And Session Lifecycle

- MEM-REQ-001: Authenticated Memory bounded context는 Conversation Session, Turn, final/provisional Entry와 projection, Summary, Data Dependency, Dispatch/Summary/Context/Purge process state, retention과 purge의 유일한 업무 mutation owner여야 한다.
- MEM-REQ-002: Gateway, Workflow Engine, Log System과 다른 도메인은 Memory persistence table을 직접 변경하지 않고 Memory application contract를 사용해야 한다.
- MEM-REQ-003: 새 대화는 기존 session을 변경하지 않고 새 session을 발급해야 한다.
- MEM-REQ-004: Authenticated internal Close는 새 runtime Memory Context read와 turn append를 즉시 차단해야 한다. 현재 subject에게 허용된 redacted transcript read와 privacy delete request는 retention 기간 동안 허용할 수 있다. Close가 delete 권리를 제거해서는 안 된다.
- MEM-REQ-005: Reset은 기존 session close와 새 session 발급을 하나의 lifecycle operation으로 처리해야 한다.
- MEM-REQ-006: Authenticated internal Delete는 접근을 즉시 차단하고 tombstone 이후 entry, summary와 dependency를 durable purge해야 한다. Legal hold 대상 content는 runtime/provider 접근에서 분리된 compliance boundary에 격리하고 hold 종료 후 purge한다.
- MEM-REQ-007: Expiry, close, reset 또는 delete 이후 도착한 queued/retried completion은 session을 다시 활성화하거나 entry를 append할 수 없어야 한다.

### Turn And Concurrency

- MEM-REQ-010: User turn은 transport `Idempotency-Key`에서 서버가 정규화한 canonical request ID와 bounded request fingerprint로 idempotent하게 시작해야 한다. Body에 별도 client request ID를 중복 요구하지 않아야 한다.
- MEM-REQ-011: Memory는 Celery/queue delivery를 at-least-once로 가정하고 동일 logical request의 중복 turn과 중복 usage를 만들지 않아야 한다.
- MEM-REQ-012: Session lifecycle, content, pending turn과 summary source version을 서로 다른 revision/version으로 관리해야 한다.
- MEM-REQ-013: 동시에 하나의 active turn만 허용하는 초기 policy 또는 동등한 명시적 ordering policy를 적용해야 한다.
- MEM-REQ-014: Complete는 pending turn version과 start 당시 lifecycle revision을 검증하고 Turn terminal 전이, Session active-turn 해제/content revision, final entry/projection 승격과 required outbox를 하나의 UnitOfWork로 commit해야 한다. Unrelated summary/projection 변경 때문에 정상 completion을 거부하지 않아야 한다.
- MEM-REQ-015: Failed/cancelled turn은 lifecycle 또는 transcript에 보존할 수 있지만 기본 LLM Memory Context에는 포함하지 않아야 한다.
- MEM-REQ-016: Pending turn 생성과 durable Turn Dispatch Job 저장은 같은 DB transaction에서 원자적으로 처리해야 한다.
- MEM-REQ-017: Dispatch publisher 장애, Gateway crash와 ambiguous broker acknowledgement 뒤에도 reconciliation이 turn을 재발행하거나 terminal 처리해 session을 영구 점유하지 않아야 한다.
- MEM-REQ-018: Turn dispatch 재시도는 Memory-owned mutation만 idempotent하게 재적용하며 arbitrary workflow node/tool side effect를 Memory가 자동 재실행하지 않아야 한다.
- MEM-REQ-019: Memory-owned pending dispatch claim과 Workflow-owned running execution lease는 각각 server-side deadline을 가지며 만료 후 owner domain의 safe recovery state가 명시되어야 한다. Memory는 Workflow lease를 직접 갱신하지 않아야 한다.

### Conversational Mapping And Node Policy

- MEM-REQ-020: Conversation Memory를 사용하는 graph/deployment는 user turn input과 최종 assistant output mapping을 명시해야 한다.
- MEM-REQ-021: Runtime은 첫 input, 마지막 LLM node 또는 임의 Answer node를 대화 source로 추측하지 않아야 한다.
- MEM-REQ-022: Node Memory policy는 graph/deployment snapshot에 저장하고 기본값은 OFF여야 한다.
- MEM-REQ-023: Chatbot deployment type은 conversation session을 제공할 수 있지만 모든 LLM node Memory를 강제해서는 안 된다.
- MEM-REQ-024: 기본 Memory source는 completed user turn과 mapped final assistant answer로 제한해야 한다.
- MEM-REQ-025: 중간 node output은 node config가 허용한 bounded channel/projection으로만 저장해야 한다.
- MEM-REQ-026: 서버 Conversation Session 생성 surface는 별도 인증·접근 정책을 갖춘 authenticated internal Chatbot 후속 target으로 제한한다. Public Chatbot, Workflow Editor test, schedule, webhook, API batch와 비대화형 deployment는 Conversation Session을 자동 생성하지 않아야 한다.

### Public Client-Held History

- MEM-REQ-027: Public Chatbot 요청은 root 'conversation.history'에 완료된 'user'/'assistant' turn만 교대 순서로 전달하고 첫 요청은 빈 배열을 사용해야 한다.
- MEM-REQ-028: 서버는 'system', 'developer', 'tool', extra field, 빈 content, 미완성 turn, 20 turn 초과와 malformed envelope을 provider dispatch 전에 거부해야 한다.
- MEM-REQ-029: 서버는 현재 inputs와 history를 다시 계산해 4,096-token context 상한을 적용하고, 초과 시 가장 오래된 완료 turn 단위로 제거해야 한다. 현재 inputs만으로 상한을 넘으면 거부해야 한다.
- MEM-REQ-029A: Public history는 인증·인가·system policy·resource provenance·credential 또는 billing principal의 근거가 될 수 없어야 한다.
- MEM-REQ-029B: Public Chatbot은 legacy 'memory_mode'와 browser-generated 'conversation_id'를 거부하고 Conversation Session/Turn/Entry/Transcript/Access Grant를 생성·조회하지 않아야 한다.
- MEM-REQ-029C: Public WorkflowRun, WorkflowNodeRun과 Trace payload는 input/history/prompt/completion 원문을 저장하지 않고 content-free 상태·시간·usage metadata만 저장해야 한다.

### Provenance And Authorization

- MEM-REQ-030: Knowledge, connector/tool, subworkflow와 system policy adapter는 server-derived bounded Data Dependency를 `RuntimeDataDependencyEnvelope`로 결과와 함께 제공해야 한다. Condition/Switch/Loop runtime은 predicate, route, iterable, bound와 termination 판단에 사용한 dependency를 활성 control context로 전파해야 한다.
- MEM-REQ-031: Workflow Runtime은 transform/code/LLM/final output을 거치는 동안 값에 영향을 준 dependency와 해당 결과를 선택한 활성 control dependency의 합집합을 보존해야 한다. 선택된 branch의 상수 출력은 control dependency를 상속하고 선택되지 않은 branch의 값 dependency는 합산하지 않아야 한다. V1에서는 optional dependency를 지원하지 않고 canonical envelope의 모든 dependency를 필수로 취급해야 한다.
- MEM-REQ-032: Client 또는 arbitrary node payload가 canonical resource ID, authorization-safe reference나 decision revision을 선언해 Memory 권한을 높일 수 없어야 한다.
- MEM-REQ-033: Private/sensitive derived output의 provenance가 incomplete하면 Memory write를 fail-closed 해야 한다.
- MEM-REQ-034: Memory read는 current session subject/audience와 source-owning domain의 current authorization을 다시 평가해야 한다.
- MEM-REQ-035: Dependency 하나라도 거부되거나 검증 불가능하면 entry 전체를 제외해야 하며 denied source identity와 exact count를 client에 노출하지 않아야 한다.
- MEM-REQ-036: Summary는 source entry dependency의 합집합과 retention/invalidation lifecycle을 상속해야 한다.
- MEM-REQ-037: Resource revoke/delete event는 관련 entry, summary와 미사용 context lease를 선제 무효화할 수 있어야 하며 read-time authorization을 대체해서는 안 된다.
- MEM-REQ-038: Main LLM provider adapter는 provider 호출 직전에 short-lived Memory Context authorization lease를 provider attempt로 claim하고 current authorization을 재검증한 operation 안에서만 raw context를 materialize해야 한다.
- MEM-REQ-039: Summary generation lease도 source authorization decision revision set에 binding하고 summarizer adapter가 provider 호출 직전에 current authorization과 lease generation을 재검증해야 한다.

### Public And Authenticated Boundary

- MEM-REQ-040: Public Chatbot은 anonymous request이며 authenticated subject, organization membership 또는 private Memory 권한을 합성하지 않아야 한다.
- MEM-REQ-041: Public history가 login cookie나 optional authorization header와 함께 도착해도 private Memory·Knowledge 권한을 부여하지 않아야 한다.
- MEM-REQ-042: Public Chatbot과 authenticated internal Chatbot은 UI 일부를 재사용할 수 있어도 backend route, execution subject, storage namespace와 retention policy를 분리해야 한다.
- MEM-REQ-043: Public history를 로그인 후 authenticated durable session으로 자동 승격·병합하지 않아야 한다.
- MEM-REQ-044: Authenticated session은 current user execution subject, organization, workflow/deployment scope를 서버가 canonical하게 구성해야 한다.
- MEM-REQ-045: Public Chatbot lifecycle API(create/close/reset/delete/transcript/purge-status)를 등록하지 않아야 하며 reset은 Client가 local history를 폐기하는 동작이어야 한다.
- MEM-REQ-046: Public history는 browser memory에만 유지하고 localStorage/sessionStorage에 자동 복구용 원문을 저장하지 않아야 한다.
- MEM-REQ-047: Public request/response와 validation error는 'Cache-Control: no-store', 'Referrer-Policy: no-referrer'를 유지하고 CORS grant를 제공하지 않아야 한다.
- MEM-REQ-048: Workflow task args representation, application log, audit, trace와 metric label에 Public history 원문을 남기지 않아야 한다.
- MEM-REQ-049: 인증형 내부 Chatbot durable Memory는 RBAC, CSRF/Origin, retention/legal policy와 operator transcript authorization을 별도 후속 이슈에서 완결해야 한다.

### Context, Summary And Cost (Authenticated Internal Target)

- MEM-REQ-050: BuildMemoryContext는 LLM Credentials가 사전에 발급한 `purpose=main_generation` ProviderExecutionCapability의 opaque identity/revision, bounded turn/token policy, current authorization과 node channel을 적용한 Context Materialization Plan handle과 single-active-attempt authorization lease를 반환해야 한다. Raw context는 provider adapter가 같은 capability와 provider attempt로 lease를 claim할 때만 획득해야 하며, claim 결과는 materialized context에 대응하는 server-derived RuntimeDataDependencyEnvelope를 함께 반환해야 한다. Same-attempt retry만 idempotent하게 허용해야 한다.
- MEM-REQ-051: Window 범위 안에서는 불필요한 summary provider 호출을 생략할 수 있어야 한다.
- MEM-REQ-052: 동일 session/channel/source revision/policy/summarizer version의 동시 summary 요청은 provider 호출 전 generation lease로 단일화해야 한다.
- MEM-REQ-053: Main/summary model, credential과 data egress는 LLM Credential domain이 발급한 `ProviderExecutionCapability`만 사용해야 한다. 초기 summary policy는 node의 승인된 provider/model/credential을 상속하는 `inherit_node`만 지원하고 별도 preset 결정 전 `organization_default`를 지원하지 않아야 한다.
- MEM-REQ-054: Summary 예상 비용은 provider 호출 전에 LLM Credentials가 발급한 ProviderExecutionCapability의 opaque identity/revision과 Budget이 요구하는 purpose/pricing/cap binding으로 원자적으로 reserve하고 실제 usage를 idempotent하게 commit/reconcile 해야 한다.
- MEM-REQ-055: Provider 성공, summary CAS, usage commit과 audit/outbox의 부분 실패를 durable generation state와 reconciliation으로 처리해야 한다.
- MEM-REQ-056: Usage/reconciliation terminal 전 summary를 새 Memory Context에서 재사용하지 않아야 한다.
- MEM-REQ-057: Memory summary usage는 main node usage와 구분되는 `purpose=memory_summary` 또는 동등한 typed purpose로 귀속해야 한다.
- MEM-REQ-058: Node Memory read/summary failure policy와 required conversation turn write failure policy를 분리해야 한다.
- MEM-REQ-059: Summary generation lease는 재획득마다 증가하는 fencing generation을 가지며 current generation과 source revision이 일치하는 owner만 summary를 commit할 수 있어야 한다. Stale owner가 이미 수행한 provider attempt의 actual usage는 summary 채택 여부와 분리해 attempt idempotency key로 정확히 한 번 reconcile해야 한다.

### Transcript, Retention And Privacy

- MEM-REQ-060: Public visible transcript의 source of truth는 현재 브라우저 memory이며 서버 transcript projection을 제공하지 않아야 한다.
- MEM-REQ-061: Public reset/new conversation은 Client history 폐기만 수행하고 server delete/purge lifecycle을 만들지 않아야 한다.
- MEM-REQ-062: Public 실행 metadata는 대화 원문, prompt, completion, browser history와 client-generated identity를 포함하지 않아야 한다.
- MEM-REQ-063: Celery broker/result의 요청 처리 중 일시 전달은 business persistence로 사용하지 않고 args representation을 redaction하며, result는 소비 직후 제거하고 장애 시 기존 TTL 상한을 따라야 한다.
- MEM-REQ-064: Authenticated internal Chatbot의 Memory content는 잠재적 민감 데이터로 취급하고 encryption-at-rest, backup/export, operator access와 retention을 적용해야 한다.
- MEM-REQ-065: Raw secret, credential, unrestricted trace payload와 raw private source identity는 Public history나 internal Memory content·audit·observability metadata에 포함될 수 없어야 한다.
- MEM-REQ-066: Internal durable Memory의 transcript, close/reset/delete/purge와 legal hold 계약은 Public stateless flow에 재사용하지 않고 별도 authenticated surface에만 적용해야 한다.

### Migration And Compatibility

- MEM-REQ-070: Dedicated Memory store를 source of truth로 사용하고 Workflow execution log를 target Memory reader로 사용하지 않아야 한다.
- MEM-REQ-071: Provenance 없는 legacy history는 기본 Memory Context에서 fail-closed 해야 한다.
- MEM-REQ-072: Deployment/session의 immutable deployment version 또는 snapshot hash, conversation mapping/Memory policy version, Memory contract version과 storage generation을 고정하고 동일 session에서 다른 deployment version이나 legacy/new writer를 혼용하지 않아야 한다.
- MEM-REQ-073: Unsupported Worker가 새 Memory contract deployment를 실행하지 못하도록 deployment activation/preflight에서 차단해야 한다.
- MEM-REQ-074: Multi-LLM legacy graph의 primary node와 final answer를 자동 추측해 migration하지 않아야 한다.
- MEM-REQ-075: Global `memory_mode`와 reserved business input 방식은 migration 완료 후 제거해야 한다.
- MEM-REQ-076: Memory task envelope은 contract/storage generation과 minimum worker capability를 포함하고 Worker가 외부 side effect 전에 검증해야 한다.
- MEM-REQ-077: Rolling migration 중 target Memory task는 capability가 확인된 versioned queue 또는 전용 worker pool만 소비해야 하며 activation preflight만으로 mixed worker compatibility를 가정하지 않아야 한다.
- MEM-REQ-078: Dispatch claim/publish/admission observation/reconciliation은 Memory application command로만 전이해야 하며 persistence/Celery adapter가 state policy를 직접 결정하지 않아야 한다.
- MEM-REQ-079: Workflow execution admission은 Workflow domain이 dispatch ID로 중복 제거하고 execution lease/heartbeat를 소유해야 한다. Memory acknowledgement 유실은 admission lookup/reconciliation으로 복구해야 한다.
- MEM-REQ-079A: Dormant Public lifecycle foundation은 ADR-0074를 대체하는 별도 Accepted ADR 없이 route 또는 runtime에 재연결할 수 없다. Authenticated internal Memory 활성화는 필요한 table·column capability를 실제 DB schema에서 검증하고 누락 또는 introspection 실패를 fail-closed해야 한다.

### Provider Attempt Reliability And Summary Pricing

- MEM-REQ-080: Context handle은 raw text 복사본이 아니라 ordered entry/summary reference, policy version, server-keyed content digest와 version으로 구성된 short-lived materialization plan을 가리켜야 한다. Digest를 client/telemetry에 노출하지 않아야 한다.
- MEM-REQ-081: Provider adapter는 server-issued provider attempt ID와 matching ProviderExecutionCapability로 context lease를 claim하고 current authorization을 재검증한 같은 trusted operation에서 raw context를 materialize해야 한다.
- MEM-REQ-082: 같은 provider attempt의 lease claim retry는 idempotent해야 하며 다른 attempt가 claim을 탈취하지 못해야 한다.
- MEM-REQ-083: Provider adapter는 outbound call 직전에 `provider_started`를 durable하게 기록해야 하며 marker commit이 실패하면 provider를 호출하지 않아야 한다. Claim 후 start 전 crash는 claim expiry 뒤 새 lease/attempt로 재승인할 수 있어야 한다.
- MEM-REQ-084: `provider_started` 이후 outcome unknown은 provider를 자동 재호출하지 않고 usage/result reconciliation 또는 safe node failure로 닫아야 한다.
- MEM-REQ-085: Summary model 가격을 산정할 수 없거나 estimate가 invalid/unknown-zero이면 reservation을 거부하고 provider를 호출하지 않아야 한다. Memory adapter가 임의 가격 또는 0원 fallback을 만들지 않아야 한다.

### Cross-Domain Capability, Version And Purge Contracts (Authenticated Internal Target)

- MEM-REQ-087: Active deployment version이 변경되면 기존 authenticated session은 자동 migration하지 않고 typed conflict와 safe new-session action을 제공해야 한다.
- MEM-REQ-088: ProviderExecutionCapability schema와 credential permission revision은 LLM Credentials domain이 소유하고 Memory는 opaque identity/revision과 session/deployment/node/purpose/provider-attempt binding만 소비해야 한다.
- MEM-REQ-089: Main/summary provider call, Memory context lease, budget reservation과 usage reconciliation은 같은 capability identity/revision을 검증해야 한다.
- MEM-REQ-090: Source authorization bulk result는 decision, principal kind와 opaque authorization/resource/policy revision을 제공하고 missing/unknown은 fail-closed해야 한다.
- MEM-REQ-091: Internal purge는 session/turn/entry/summary/context content와 sensitive dependency reference를 지우고 content-free operational metadata만 bounded retention 동안 유지해야 한다.
- MEM-REQ-092: Legal hold와 physical erasure completion은 organization governance와 별도 compliance process가 소유해야 한다.
- MEM-REQ-093: Internal lifecycle audit는 실제 authenticated actor와 canonical organization/session scope를 사용하고 execution/credential/billing principal을 actor로 대체하지 않아야 한다.

## Non-Functional Requirements

- MEM-NFR-001: Public history bound는 DB, FastAPI, Celery와 provider 없이 단위 테스트할 수 있어야 한다.
- MEM-NFR-002: Public request history는 20 turn, 4,096 token, message char와 envelope byte 상한을 가져야 한다.
- MEM-NFR-003: Public task args representation과 error는 원문을 노출하지 않아야 한다.
- MEM-NFR-004: Public WorkflowRun/NodeRun/Trace content capture는 fail-closed로 비활성화하고 metadata logging 장애가 provider 실행을 무기한 점유하지 않아야 한다.
- MEM-NFR-005: Public 성공·validation·router 오류는 no-store/no-referrer이며 CORS grant를 제공하지 않아야 한다.
- MEM-NFR-006: Authenticated internal Memory store, authorization, summarizer와 budget adapter는 bounded query/timeout을 사용해야 한다.
- MEM-NFR-007: Memory package import가 infrastructure startup side effect를 만들지 않아야 한다.

## Required Initial Policy Baseline

| Policy | Public safe default |
| --- | --- |
| Completed history | 최대 20 turn |
| History roles | user/assistant 완료 pair만 |
| Conversation context | 현재 inputs 포함 최대 4,096 tokens |
| Message content | message당 최대 32,768 characters |
| Encoded history envelope | 최대 131,072 bytes |
| Browser persistence | React memory only |
| Server conversation persistence | 없음 |
| Workflow run/node/trace content | 저장 금지 |
| Transport response result | 소비 직후 제거, 장애 시 기존 최대 1시간 TTL |

Public 값을 완화하려면 별도 보안·비용 검토가 필요하다. Authenticated internal Chatbot의 session expiry, retention, concurrency, summary, purge와 legal-hold 기본값은 후속 이슈에서 조직 governance와 함께 확정한다.

## Current Implementation Gap

MBA-318은 Public client-held history, legacy Public memory control 차단, content-free Workflow logging과 Embed Chat 전달을 구현한다. MBA-316/317의 durable persistence 및 public lifecycle foundation은 active API에 등록하지 않고 보존한다.

남은 범위는 authenticated internal Chatbot의 RBAC/CSRF/Origin, durable session/turn/entry, transcript lifecycle, retention/legal hold, provider admission/lease/fencing과 운영 UI다. 이 후속 구현은 active durable Memory domain contract를 최신 `dev`와 ADR-0074 경계에 맞게 선별 적용한다.

## MBA-318 Boundary Completion Requirements

- MEM-REQ-097: Public Chatbot 배포는 history를 소비할 정확한 `llmNode` canonical location을 versioned deployment config로 지정해야 하며 first/last node를 자동 추측하지 않아야 한다.
- MEM-REQ-098: Gateway와 Worker는 deployment snapshot에서 consumer mapping을 각각 검증하고 LLMNode는 자신의 canonical location이 일치할 때만 history를 provider와 RAG query에 사용해야 한다.
- MEM-REQ-099: Public RAG audit actor는 `actor_id=null`, `actor_type=public`이어야 하며 app owner, credential principal 또는 generic system actor로 대체하지 않아야 한다.
- MEM-REQ-100: Public token validation은 실제 tokenizer만 사용하고 tokenizer를 사용할 수 없으면 문자 휴리스틱 없이 `conversation.token_count_unavailable`로 fail-closed해야 한다.
- MEM-REQ-101: Public envelope, legacy control과 consumer mapping 검증은 budget admission, secret migration, DB mutation과 task publish보다 먼저 완료해야 한다.
- MEM-REQ-102: 혼합 revision 동안 public info capability가 client-history 지원 여부를 나타내야 하며 capability가 없는 구 Gateway와 구 Client 조합도 가용해야 한다.
- MEM-REQ-103: 새 Gateway가 legacy root public Chatbot 요청을 호환 처리할 때 legacy control을 제거하고 server Memory와 content persistence를 활성화하지 않는 무상태 실행이어야 한다.
- MEM-REQ-104: strict rollout mode에서는 root public Chatbot 실행을 허용하지 않고 전용 `/chat` history 계약만 허용해야 한다.
- MEM-REQ-105: Public raw history는 bounded TTL의 일회성 transient store에만 두고 broker에는 opaque reference만 전달해야 하며, task는 Gateway 생성 시각 기준 bounded absolute deadline과 broker expiry를 가져야 한다.
- MEM-REQ-106: Worker는 DB 조회, Knowledge sync, Workflow Engine과 provider 호출 전에 deadline을 재검증하고 expired/malformed public task를 non-retryable하게 거부해야 한다.
- MEM-REQ-107: Worker는 queued `public_chat_history` 원문을 DB와 external I/O 전에 fail-closed하고, raw history는 validated opaque reference를 atomic consume한 뒤 invocation-local context에만 materialize해야 한다.
- MEM-REQ-108: Redis consume의 `store_unavailable`은 history 소비가 확인되기 전 기존 bounded Celery retry를 사용하되 invalid/missing/corrupt 또는 소비 후 오류는 자동 replay하지 않아야 한다.
- MEM-REQ-109: Public Client는 조회한 active deployment version을 root와 `/chat` 요청에 결박해야 한다. Gateway mismatch는 부수효과 전에 safe conflict로 종료하며 Client는 public info를 no-store로 갱신하고 이전 version history를 폐기한 뒤 현재 입력을 한 번만 재시도해야 한다.
- MEM-REQ-110: Public current `inputs`는 UTF-8 canonical JSON 131,072-byte 상한을 tokenization 전에 검증하고, `/chat` HTTP body는 393,216-byte 상한을 JSON parsing 전에 적용해야 한다.
- MEM-REQ-111: Raw history admission과 sanitizer 이후 projection validation은 별도 단계여야 한다. 정제로 비게 된 완료 pair는 함께 제거하고 정제로 늘어난 internal marker는 raw message/envelope 상한을 다시 적용하지 않은 채 final projection token 상한으로 제한해야 한다.
- MEM-REQ-112: Public Client는 완료 응답을 history에 넣기 전에 server와 같은 Unicode scalar/message 32,768-character 상한과 131,072-byte UTF-8 history envelope 상한을 적용해야 한다. 화면에는 표시된 oversized 응답도 이후 요청 history에서는 제외해야 한다.
- MEM-REQ-113: Public transient execution은 versioned task name과 전용 queue에서만 publish·consume해야 한다. 일반 `workflow.execute`는 public marker가 있는 misrouted task를 DB·Redis·외부 I/O 전에 `conversation.task_contract_mismatch`로 거부해야 한다.
- MEM-REQ-114: 배포 중 새 Worker는 일반 workflow queue와 Public 전용 queue를 함께 소비하되, 구 Worker가 일반 queue에서 Public payload를 처리할 수 없도록 Gateway가 Public task를 일반 queue에 publish하지 않아야 한다.
- MEM-REQ-115: 전용 Public `/chat` 요청은 positive integer `deployment_version`을 필수로 보내야 하며 Gateway는 누락·형식 오류를 transient store와 task publish 전에 `conversation.deployment_version_invalid`로 거부해야 한다. Compatibility root route만 version 생략을 허용한다.
- MEM-REQ-116: Public RAG query embedding은 한 요청에서 여러 provider/model group을 순차 호출할 때 각 provider invoke 직전에 공통 absolute deadline을 다시 검사하고 만료 뒤 추가 외부 I/O를 시작하지 않아야 한다.
- MEM-REQ-117: Gateway의 transient history 저장은 async Redis 호출과 명시적 bounded timeout을 사용해야 하며 Redis 지연이 Uvicorn event loop를 동기적으로 점유하지 않아야 한다.
- MEM-REQ-118: `PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE`는 표준 Docker Compose와 Helm values→ConfigMap→Gateway container env 경로에서 설정 가능해야 한다.
- MEM-REQ-119: Public `/chat` reverse proxy read/send timeout은 Gateway·Worker absolute request deadline보다 길어야 하며 proxy가 먼저 연결을 끊은 뒤 provider 실행이 계속되는 경로를 만들지 않아야 한다.
- MEM-REQ-120: Reverse proxy가 Public `/chat` body 상한을 먼저 적용할 때도 `413 conversation.request_too_large`, `Cache-Control: no-store`, `Referrer-Policy: no-referrer`의 content-free 계약을 반환해야 한다.

`PUBLIC_CHAT_CONVERSATION_ROLLOUT_MODE=compatibility`는 배포 순서용 임시 기본값이다. strict 전환 전 active legacy public Chatbot을 consumer mapping이 있는 새 deployment version으로 교체한다.
