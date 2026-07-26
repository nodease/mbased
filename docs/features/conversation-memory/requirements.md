# Conversation Memory Requirements

Status: Draft
Related Features: workflow, chatbot-deployment, deployment, knowledge, connectors, organization, llm-credentials, budget-management, audit-tracing

## Purpose

Workflow와 Chatbot의 여러 turn에서 필요한 대화 맥락을 독립 Memory bounded context가 안전하게 저장·조회·요약하고, node별 policy에 따라 LLM 실행에 제공한다.

이 문서는 [ADR-0030](../../decisions/ADR-0030-memory-bounded-context.md)과 [ADR-0033](../../decisions/ADR-0033-conversation-memory-contract-completion.md)의 기능 계약을 구체화한다. 현재 `memory_mode`, browser-generated `conversation_id`, Workflow execution log 재조회 방식은 migration 전 legacy 동작이며 목표 source of truth가 아니다.

## Scope

포함:

- Workflow/Chatbot Conversation Session lifecycle
- user/final assistant turn과 명시적 node projection
- LLM node별 Memory read/write policy
- dedicated Memory store와 read-your-writes
- provenance, current authorization와 context lease
- public/authenticated session 격리
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
- Public Chatbot Visitor: bearer capability로 자신의 public conversation을 이어간다.
- Authenticated User: current organization/resource 권한으로 내부 conversation을 실행한다.
- Workflow Runtime: turn lifecycle과 Memory Context use case를 호출한다.
- Organization Manager: organization membership과 governance policy에 따라 retention 및 운영 상태를 관리한다.
- Workflow Operator: 자신에게 부여된 workflow/deployment 권한 범위에서 session 운영 상태를 확인하되 organization governance policy를 변경하지 않는다.
- Auditor: raw Memory content 없이 lifecycle·정책 결정의 safe metadata를 확인한다.

## Functional Requirements

### Domain Ownership And Session Lifecycle

- MEM-REQ-001: Memory bounded context는 Conversation Session, Access Grant, Turn, final/provisional Entry와 projection, Summary, Data Dependency, Dispatch/Summary/Context/Purge process state, retention과 purge의 유일한 업무 mutation owner여야 한다.
- MEM-REQ-002: Gateway, Workflow Engine, Log System과 다른 도메인은 Memory persistence table을 직접 변경하지 않고 Memory application contract를 사용해야 한다.
- MEM-REQ-003: 새 대화는 기존 session을 변경하지 않고 새 session을 발급해야 한다.
- MEM-REQ-004: Close는 새 runtime Memory Context read, turn append와 reset 같은 content/runtime mutation을 즉시 차단해야 한다. 소유자 또는 transcript-only grant의 redacted transcript read와 privacy delete request는 retention 기간 동안 허용할 수 있다. Close가 delete 권리를 제거해서는 안 된다.
- MEM-REQ-005: Reset은 기존 session close와 새 session 발급을 하나의 lifecycle operation으로 처리해야 한다.
- MEM-REQ-006: Delete는 접근을 즉시 차단하고 tombstone 이후 entry, summary, dependency와 access grant를 durable purge해야 한다. Legal hold 대상 content는 runtime/provider 접근에서 분리된 compliance boundary에 격리하고 hold 종료 후 purge한다.
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
- MEM-REQ-019A: Public conversation dispatch는 dispatch당 하나의 Workflow-owned durable admission으로 수렴해야 한다. Workflow execution 상태는 explicit public principal과 safe opaque correlation만 가진 content-free durable projection으로 기록하고 raw input/output column을 두지 않아야 한다. Broker와 Celery result에는 opaque organization/dispatch/turn/contract reference만 포함하고 raw input, Access Grant ID/token, context, provider response와 final output을 포함하지 않아야 한다. Cross-domain acknowledgement, provider I/O와 Memory completion 동안 어느 domain의 DB session이나 row lock도 유지하지 않아야 한다.

### Conversational Mapping And Node Policy

- MEM-REQ-020: Conversation Memory를 사용하는 graph/deployment는 user turn input과 최종 assistant output mapping을 명시해야 한다.
- MEM-REQ-021: Runtime은 첫 input, 마지막 LLM node 또는 임의 Answer node를 대화 source로 추측하지 않아야 한다.
- MEM-REQ-022: Node Memory policy는 graph/deployment snapshot에 저장하고 기본값은 OFF여야 한다.
- MEM-REQ-023: Chatbot deployment type은 conversation session을 제공할 수 있지만 모든 LLM node Memory를 강제해서는 안 된다.
- MEM-REQ-024: 기본 Memory source는 completed user turn과 mapped final assistant answer로 제한해야 한다.
- MEM-REQ-025: 중간 node output은 node config가 허용한 bounded channel/projection으로만 저장해야 한다.
- MEM-REQ-026: 초기 Session 생성 surface는 public Chatbot으로 제한해야 한다. 별도 인증·접근 정책을 갖춘 authenticated internal Chatbot은 후속 target이며, Workflow Editor test, schedule, webhook, API batch와 비대화형 deployment는 각각의 인증·CSRF·idempotency·retention 계약 없이는 Conversation Session을 자동 생성하지 않아야 한다.
- MEM-REQ-027: 초기 runtime은 root의 정확한 `Start -> LLM -> Answer` topology, 하나의 required text input, 하나의 mapped text output과 하나의 fixed-model LLM node만 허용해야 한다. Routing/fallback/tool/Knowledge/RAG/structured output/nested graph/summary와 unknown active behavior는 같은 pure validator로 preflight와 runtime에서 provider I/O 전에 거부해야 한다. Frozen graph/config가 Memory-on을 요청하면 invalid contract 또는 conversation envelope 누락을 Memory-OFF/legacy 실행으로 완화하지 않아야 한다.

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

- MEM-REQ-040: Public conversation token은 server-issued opaque bearer capability여야 하며 raw token과 internal session ID를 분리해야 한다.
- MEM-REQ-041: Public token 원문은 Access Grant source-of-truth에 저장하지 않고 verifier hash, session/deployment ID·version/audience binding, `active|transcript_only|revoked|expired` state와 expiry만 관리해야 한다. V1은 standalone rotation endpoint, rotated grant chain과 old/new grant grace window를 지원하지 않는다. 별도 idempotency response store의 application-encrypted replay record는 최대 10분 TTL 예외이며 Memory-owned bounded retention worker가 만료 row를 live store에서 물리 삭제해야 한다. Worker는 만료 replay child와 일반 idempotency retention이 끝난 parent record에 각각 독립된 bounded batch quota를 보장하고, parent 삭제 시 종속 replay row도 함께 제거해 scope/key uniqueness가 영구 잠기지 않게 해야 한다. Backup에서 즉시 복구 불가능하다는 보장은 승인된 외부 crypto-erasure 또는 database-backup 미사용 계약이 확인된 환경에 한정하며, 확인되지 않은 환경에서는 public lifecycle을 활성화하지 않아야 한다. TTL 뒤 same-key replay는 새 secret/grant를 만들거나 rotation하지 않고 `memory.secret_replay_expired` conflict를 반환해야 한다.
- MEM-REQ-042: Public Access Grant와 purge receipt 원문을 URL/query, audit, trace, metric label과 application log에 남기지 않아야 하며, 구조화 field뿐 아니라 자유 텍스트에 포함된 versioned opaque value도 공통 redaction 경계에서 마스킹해야 한다.
- MEM-REQ-043: Public session을 로그인 후 authenticated session으로 자동 승격·병합하지 않아야 한다.
- MEM-REQ-044: Authenticated session은 current user execution subject, organization, workflow/deployment scope를 서버가 canonical하게 구성해야 한다. Credential principal, billing principal과 audit actor는 execution subject와 별도로 파생해야 한다.
- MEM-REQ-045: Public bearer capability 또는 public route의 optional authentication header가 private Memory와 private Knowledge 권한을 부여해서는 안 된다. `public_chatbot`과 `authenticated_internal_chatbot`은 시각 컴포넌트를 재사용할 수 있어도 backend route, 인증/CORS/Origin, deployment access policy와 session namespace를 분리해야 한다.
- MEM-REQ-046: 로그아웃 후 authenticated session을 public endpoint에서 이어갈 수 없어야 한다.
- MEM-REQ-047: Public token은 CSPRNG로 생성한 최소 128-bit entropy의 versioned opaque token이어야 하며 server-side verifier는 HMAC 같은 keyed one-way verifier 또는 승인된 memory-hard password hash와 constant-time comparison을 사용해야 한다. Verifier key 교체는 새 값 발급에 사용하는 active key와 검증 전용 previous key 최대 한 개로 제한하고, previous key로 발급된 live grant/receipt가 남아 있는 동안 원래 state·scope·expiry 안에서 검증할 수 있어야 한다. 이는 standalone grant rotation이나 grace가 아니다.
- MEM-REQ-048: Public session 생성은 deployment, organization과 deployment+network source별 finite rate limit을 적용하고 아직 존재하지 않는 grant의 전역 placeholder bucket을 만들지 않아야 한다. Grant 발급 이후 lifecycle/run은 deployment, organization, network source와 실제 grant별 finite rate, concurrency, turn/content와 비용 한도를 가져야 하며 운영 설정이 누락되어도 무제한으로 완화되지 않아야 한다. 같은 canonical operation/scope/idempotency key/fingerprint의 concurrent retry는 admission budget을 한 번만 소비하고 다른 fingerprint는 별도 요청으로 계산해야 한다. 외부 admission I/O 동안 DB transaction이나 row lock을 유지하지 않으며, mutation transaction은 admission 뒤 current scope/state를 다시 검증해야 한다.
- MEM-REQ-049: Cookie 기반 authenticated mutation은 CSRF token, exact allowed Origin과 Fetch Metadata를 검증하고, CORS grant를 제공하지 않는 public same-origin iframe API 경계와 분리해야 한다.
- MEM-REQ-041A: Cleanup 지연과 무관하게 retention expiry에 도달한 idempotency row는 replay·conflict 대상에서 제외하고, 동일 scope/key의 새 reservation은 row lock 아래 만료 claim을 제거한 뒤 원자적으로 생성해야 한다. Secret exact replay는 필요한 purge/replay row lock을 모두 획득한 뒤 새 server time으로 idempotency parent retention과 replay child expiry를 다시 확인해야 하며 요청 시작 시각으로 잠금 대기 중 경과한 TTL을 연장해서는 안 된다. Periodic retention은 parent/child별 독립 quota를 유지하면서 포화된 batch를 finite per-run budget까지 반복하고 남은 backlog를 다음 schedule에 이어 처리해야 한다.
- MEM-REQ-048A: Same-key/same-fingerprint retry는 logical admission quota를 다시 소비하지 않지만 별도의 finite per-request retry bucket을 통과해야 한다. Fixed-window counter는 정확한 현재 window boundary에서 만료되어 이전 window 사용량을 이월하지 않아야 하며, request composition은 process-scoped Redis pool을 재사용해야 한다. Mutation은 App/grant/session row lock을 모두 획득한 다음 새 server time으로 expiry와 current state를 재검증해야 한다.
- MEM-REQ-049A: Canonical Public Conversation route와 framework가 허용하는 trailing-slash redirect alias는 동일한 outer CORS boundary를 통과해야 한다.

### Context, Summary And Cost

- MEM-REQ-050: BuildMemoryContext는 LLM Credentials가 사전에 발급한 `purpose=main_generation` ProviderExecutionCapability의 opaque identity/revision, bounded turn/token policy, current authorization과 node channel을 적용한 Context Materialization Plan handle과 single-active-attempt authorization lease를 반환해야 한다. Raw context는 provider adapter가 같은 capability와 provider attempt로 lease를 claim할 때만 획득해야 하며, claim 결과는 materialized context에 대응하는 server-derived RuntimeDataDependencyEnvelope를 함께 반환해야 한다. Same-attempt retry만 idempotent하게 허용해야 한다.
- MEM-REQ-050A: BuildMemoryContext는 raw content를 읽지 않고 현재 Turn 이전 completed user/assistant pair의 bounded reference snapshot만 생성해야 한다. Claim은 newest-first contiguous pair를 검증하고 missing, ineligible 또는 serialized token budget 초과 pair에서 중단하며 더 오래된 pair로 보충하지 않아야 한다. 선택 pair는 시간순으로 materialize하고, candidate가 없는 경우에만 명시적 complete-empty dependency envelope을 허용해야 한다.
- MEM-REQ-050B: Materialized history는 current user message 바로 앞의 bounded untrusted history block으로만 삽입해야 한다. Memory content를 system/developer instruction으로 승격하거나 Workflow node가 소유한 prompt template과 합쳐 저장하지 않아야 한다.
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

- MEM-REQ-060: 사용자 transcript와 모델용 Memory Context를 별도 projection으로 취급해야 한다.
- MEM-REQ-061: Transcript는 current actor/capability가 볼 수 있는 redacted display entry만 반환해야 한다.
- MEM-REQ-062: Transcript에 보이는 turn이 current authorization, token budget 또는 summary policy로 Memory Context에서 제외될 수 있음을 UI가 오해 없이 처리해야 한다.
- MEM-REQ-063: Reset/delete 후 visible transcript와 server session lifecycle이 일치해야 한다.
- MEM-REQ-064: Redaction을 통과한 Memory content도 잠재적으로 민감한 데이터로 취급하고 최대 크기, encryption-at-rest, backup/export와 operator access policy를 적용해야 한다.
- MEM-REQ-065: Raw secret, credential, unrestricted trace payload와 raw private source path/title은 Memory content, audit 또는 observability metadata가 될 수 없어야 한다. Public Access Grant와 purge receipt는 필드명뿐 아니라 versioned opaque value가 자유 텍스트에 포함된 경우에도 공통 tracing/redaction 경계에서 항상 secret으로 마스킹해야 한다.
- MEM-REQ-066: Audit/Tracing은 safe session/entry reference, action, status, reason과 bucketed count만 기록하고 raw Memory content를 저장하지 않아야 한다. AuditLog는 `memory.session.*`/`memory.grant.*` 관리·보안 lifecycle에 제한하고 정상 turn/summary 상태는 operational trace/metric으로 기록해야 한다.
- MEM-REQ-067: Memory content는 shared privacy classification/redaction capability를 사용해야 하며 Memory domain이 PII/secret 판별 규칙을 자체 복제하지 않아야 한다.
- MEM-REQ-068: Close 후 transcript 허용 여부, reset 이후 이전 transcript 표시와 delete purge 상태는 runtime Memory Context 접근과 별도 정책으로 평가해야 한다.
- MEM-REQ-069: Delete가 비동기 purge를 시작하면 caller가 raw session/grant 없이도 완료·실패·재시도 상태를 확인할 수 있는 scoped receipt를 제공해야 한다. Purge Job은 organization, stable App ID, 발급 시점 deployment ID/version과 audience snapshot을 durable하게 보존해 Session/Grant row가 물리 삭제된 뒤에도 receipt scope를 검증해야 한다. 같은 App의 재배포는 receipt를 무효화하지 않아야 하고 URL slug가 다른 App으로 재할당되면 조회를 resource-hiding으로 거부해야 한다. Delete exact replay는 최대 24시간 동안 stable App과 versioned access-token verifier에 결합된 content-free authorization tombstone을 사용할 수 있지만 raw token은 저장하지 않아야 한다. Public purge는 발급 후 7일 안에 terminal 상태로 전이하고 receipt는 terminal 후 최소 24시간, 최대 발급 후 8일까지 유효해야 한다. `completed_with_hold`는 compliance 격리 완료이며 물리 삭제 완료로 표시해서는 안 된다. 이 terminal 전이를 수행할 physical purge worker가 배포에서 준비됐다는 명시적 activation gate가 확인되지 않으면 public lifecycle 전체를 fail-closed로 비활성화해야 한다.

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
- MEM-REQ-079A: Public lifecycle feature를 활성화할 때 process는 route를 제공하기 전에 필요한 Memory table·column capability를 실제 DB schema에서 검증하고 누락 또는 introspection 실패를 fail-closed해야 한다. 준비 상태를 특정 Alembic revision 문자열이나 현재 head와의 일치로 판정해서는 안 된다.

### Provider Attempt Reliability And Summary Pricing

- MEM-REQ-080: Context handle은 raw text 복사본이 아니라 ordered entry/summary reference, policy version, server-keyed content digest와 version으로 구성된 short-lived materialization plan을 가리켜야 한다. Digest를 client/telemetry에 노출하지 않아야 한다.
- MEM-REQ-081: Provider adapter는 server-issued provider attempt ID와 matching ProviderExecutionCapability로 context lease를 claim하고 current authorization을 재검증한 같은 trusted operation에서 raw context를 materialize해야 한다.
- MEM-REQ-082: 같은 provider attempt의 lease claim retry는 idempotent해야 하며 다른 attempt가 claim을 탈취하지 못해야 한다.
- MEM-REQ-083: Provider adapter는 outbound call 직전에 `provider_started`를 durable하게 기록해야 하며 marker commit이 실패하면 provider를 호출하지 않아야 한다. Claim 후 start 전 crash는 claim expiry 뒤 새 lease/attempt로 재승인할 수 있어야 한다.
- MEM-REQ-083A: Memory context-attempt marker는 lease lifecycle marker이고 ADR-0069 usage ledger가 canonical send authority여야 한다. Raw context claim과 full-request 검증 뒤 usage intent, final binding 재검증, Memory marker, usage `provider_started`, provider I/O 순서를 지켜야 한다. Memory-marker-only + exact usage intent는 current owner의 same-attempt continuation만 허용하고, usage started/outcome-unknown/terminal 상태는 provider replay를 허용하지 않아야 한다.
- MEM-REQ-084: `provider_started` 이후 outcome unknown은 provider를 자동 재호출하지 않고 usage/result reconciliation 또는 safe node failure로 닫아야 한다.
- MEM-REQ-084A: Context attempt, Memory Turn/checkpoint, Workflow admission과 execution journal의 commit 사이 crash는 deterministic identity와 current admission fence로 bounded reconciliation해야 한다. Close, grant revoke 또는 active deployment 변경 뒤에는 active 실행 권한을 복원하지 않고 reference-only cleanup만 허용하며, ADR-0069 usage state와 provisional checkpoint를 분류해 provider replay, definitive outcome 오분류와 orphan row를 만들지 않아야 한다.
- MEM-REQ-085: Summary model 가격을 산정할 수 없거나 estimate가 invalid/unknown-zero이면 reservation을 거부하고 provider를 호출하지 않아야 한다. Memory adapter가 임의 가격 또는 0원 fallback을 만들지 않아야 한다.

### Cross-Domain Capability, Version And Purge Contracts

- MEM-REQ-086: Session은 canonical organization/app/workflow, deployment ID와 immutable version 또는 snapshot hash, conversation mapping version과 node Memory policy version에 고정해야 한다. Runtime이 현재 active deployment pointer로 기존 session을 자동 rebind해서는 안 된다.
- MEM-REQ-087: Active deployment version이 변경되면 기존 session은 자동 migration하지 않아야 한다. Public surface는 기존 session 정보를 숨기고 새 conversation을 요구하며 authenticated surface는 typed conflict와 safe new-session action을 제공해야 한다.
- MEM-REQ-088: ProviderExecutionCapability의 authoritative schema, credential principal, credential permission decision revision과 발급·revoke 검증 정책은 LLM Credentials domain이 소유해야 한다. Memory는 opaque capability identity/revision과 session/deployment version, node invocation, purpose, provider attempt binding만 소비하고 credential scope를 자체 구성해서는 안 된다.
- MEM-REQ-089: Main/summary provider call, Memory context lease, budget reservation과 usage reconciliation은 같은 ProviderExecutionCapability identity/revision을 검증해야 하며 client나 Access Grant가 scope를 확장할 수 없어야 한다. Credential revoke, permission decision revision 또는 verified relation/egress policy 변경 뒤 stale capability는 새 lease claim, reservation, provider attempt admission과 outbound call 전에 fail-closed해야 한다.
- MEM-REQ-090: Source authorization bulk result는 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at`을 제공해야 한다. Source ACL이 있는 resource의 ACL revision은 decision revision에 포함해야 한다.
- MEM-REQ-091: Source-owning adapter는 membership/team/direct permission/public visibility/source ACL/lifecycle처럼 authorization decision에 영향을 주는 값이 바뀌면 decision revision을 변경해야 한다. Public audience는 subject ID/revision을 합성하지 않고 `anonymous_public_audience` principal kind로 평가해야 한다.
- MEM-REQ-092: Purge는 Session subject/audience binding, Access Grant verifier/source row, Turn content, final/provisional Entry·projection, Summary, sensitive dependency reference, raw/materialized context cache·plan, transcript/result와 conversation access-token replay ciphertext를 지워야 한다. Purge status용 최소 opaque tombstone, verifier-hash receipt와 delete 응답 유실 복구용 encrypted receipt replay만 각각 정해진 TTL/receipt expiry까지 허용하고 Dispatch/Summary Job, Provider Attempt, Context Lease, idempotency record에서는 content와 민감 reference를 제거해야 한다.
- MEM-REQ-093: Operational process record는 bounded 운영·회계 retention 동안 opaque state/timestamp/safe reason만 유지하고, Audit/usage는 각 소유 도메인 retention을 따르되 raw content/token/private source를 포함하지 않아야 한다. Purge Job/receipt는 receipt expiry까지만 유지해야 한다. `completed`는 configured content-bearing live store/cache, conversation access-token replay와 backup/export retention contract가 삭제 또는 승인된 irreversible crypto-erasure marker를 모두 확인한 경우에만 허용해야 한다. 최소 purge-control tombstone, receipt verifier와 encrypted delete-response replay만 정해진 TTL/receipt expiry까지 예외로 남길 수 있어야 한다.
- MEM-REQ-094: `completed_with_hold`와 `terminal_failure`에는 `memory.session.purged`를 기록하지 않아야 한다. Hold 해제는 별도 compliance erasure process가 처리하고 실제 erasure가 끝난 시점에만 canonical physical-purge audit을 기록해야 한다. 이미 terminal인 public purge status를 `completed`로 변경하거나 receipt를 재발급해서는 안 된다.
- MEM-REQ-095: Public create/close/reset/delete request와 grant audit actor는 `actor_id=null`, `actor_type='public'`이어야 하며 app/deployment owner를 합성하지 않아야 한다. 비동기 physical purge/compliance erasure completion은 `actor_id=null`, `actor_type='system'`으로 기록하고 original purge request safe reference로 연결해야 한다. Authenticated actor, execution subject, credential principal과 billing principal을 서로 대체하지 않아야 한다.
- MEM-REQ-096: Lifecycle operation별 audit cardinality는 ADR-0030의 create/close/reset/delete/physical-purge matrix와 정확히 일치하고 retry/reconciliation에서 중복 row를 만들지 않아야 한다.

## Non-Functional Requirements

- MEM-NFR-001: Memory domain/application policy는 DB, FastAPI, Celery와 provider 없이 단위 테스트할 수 있어야 한다.
- MEM-NFR-002: Tenant/session/channel query는 bounded limit과 index를 사용하고 dependency별 N+1 authorization query를 만들지 않아야 한다.
- MEM-NFR-003: Memory store, authorization, summarizer와 budget adapter timeout은 Workflow worker pool을 무기한 점유하지 않아야 한다.
- MEM-NFR-004: Context, token, turn, dependency와 retry 수에는 server-side upper bound가 있어야 한다.
- MEM-NFR-005: Safe observability는 public/authenticated audience, decision reason, latency와 usage를 구분하되 raw content/token/source를 label로 사용하지 않아야 한다.
- MEM-NFR-006: 별도 Memory service가 없는 초기 배포에서도 package import가 infrastructure startup side effect를 만들지 않아야 한다.
- MEM-NFR-007: Public Conversation API의 성공, 명시적 오류, dependency/body validation 오류, router 오류와 preflight를 포함한 모든 응답은 outer transport boundary에서 `Cache-Control: no-store`와 `Referrer-Policy: no-referrer`를 사용해야 한다. Public token, transcript와 lifecycle payload의 raw token은 browser history, URL, shared cache와 telemetry에 남기지 않아야 한다.
- MEM-NFR-008: Idempotency record와 replayable secret response는 scope와 TTL이 bounded되어야 하며 평문 token을 장기 보관하지 않아야 한다. Completed lifecycle replay는 최초 성공 응답의 content-free typed lifecycle/revision snapshot만 보존하고 mutable resource의 현재 상태나 raw response payload로 재구성하지 않아야 한다.
- MEM-NFR-009: Public conversation page는 strict Content Security Policy, `Referrer-Policy: no-referrer`와 reviewed script/frame origin allowlist를 적용해 sessionStorage grant의 XSS·referrer 유출 표면을 줄여야 한다.

## Required Initial Policy Baseline

구현은 다음 기준보다 약한 기본값으로 시작하지 않는다. 구체 수치는 환경별로 더 엄격하게 설정할 수 있지만 설정 누락이 무제한 허용을 의미해서는 안 된다.

| Policy | Initial safe default |
| --- | --- |
| Public grant/session idle expiry | 24시간 |
| Public grant/session absolute expiry | 7일 |
| Completed turn per public session | 최대 100 |
| Concurrent active turn per session | 1 |
| Display/model projection per entry | 각각 최대 16 KiB UTF-8 |
| Memory context | 최대 4,096 tokens, node config가 더 낮출 수 있음 |
| Public session create | deployment+network source당 10회/10분, deployment 전체 200회/10분 |
| Public run | grant당 20회/분, deployment 전체 120회/분 |
| Organization aggregate public limit | session create 1,000회/10분, run 600회/분 |
| Raw token response replay | 암호화 저장 최대 10분 |
| General mutation idempotency record | 24시간 또는 target/session retention 종료 중 먼저 도달하는 시점까지 |
| Context lease | 30초 이내, single-active-attempt claim |
| Public purge receipt | terminal 후 최소 24시간, 발급 후 최대 8일 |

Network source는 신뢰 가능한 reverse proxy chain에서 canonicalized client network를 사용한다. IP 하나만으로 security identity를 만들지 않으며 grant/deployment/organization limit을 함께 적용한다. 위 값을 완화하려면 운영 설정 검증과 security review가 필요하다.

- 같은 session은 active turn 하나만 허용하고 추가 요청은 `409 memory.active_turn_conflict`와 active turn safe reference를 반환한다.
- Public Access Grant는 `Authorization: Conversation` header로만 전달한다. URL/query와 일반 authentication cookie에는 넣지 않는다.
- Public/authenticated session은 finite idle expiry, absolute expiry, 최대 completed turn, entry bytes와 context token bound를 가진다.
- Public create/run은 grant·deployment·network source 기준 rate/concurrency limit과 organization cost gate를 모두 통과해야 한다.
- Public create/reset의 raw token 응답은 idempotency scope 동안 암호화된 단기 replay record로만 복구한다. Reset은 old grant를 즉시 revoke하고 새 session/grant를 원자 발급하는 replacement이며 rotation/grace가 아니다. Hash-only grant row만으로 token을 재구성하지 않는다.
- Public lifecycle exact replay는 최초 성공 시점의 lifecycle, revision, contract/expiry와 필요한 previous lifecycle/revision만 typed nullable column으로 보존한다. Raw response, access token과 purge receipt는 이 snapshot에 넣지 않고 별도 bounded encrypted replay에서만 복구한다.
- Secret replay record가 만료된 same-key retry는 `409 memory.secret_replay_expired`로 닫고 새 grant/receipt를 자동 생성하지 않는다. 새 conversation은 새 idempotency key로 명시적으로 생성한다.
- Public reset/delete의 secret replay expiry는 stored scope/fingerprint, grant verifier relation과 high-entropy idempotency key가 모두 일치할 때만 노출하고 그 외에는 resource-hidden 404를 유지한다.
- Public create idempotency key는 최소 128-bit random entropy를 사용하고 hash로 식별한다. Network source 변경은 정상 retry scope를 바꾸지 않으며 raw key를 durable log에 저장하지 않는다.
- Transcript read는 closed session에서 retention 기간 동안 허용할 수 있다. Public close는 기존 grant를 transcript-only로 제한하고 reset/delete는 기존 grant를 revoke한다. Expired/delete-pending/deleted session은 숨기며 runtime context는 항상 차단한다.
- Regrant는 과거 private-derived entry의 authorization을 자동 복원하지 않는다. Current authorization과 retention을 다시 통과한 entry만 사용할 수 있다.
- Summary provider 호출은 approved egress capability와 atomic budget reservation을 모두 확보해야 한다. Reservation 미지원 환경은 `window`만 허용한다.
- Summary 가격을 산정할 수 없으면 reservation을 거부하고 `window` 또는 configured failure policy로 닫는다. Unknown price를 0원으로 처리하지 않는다.
- Context lease는 short-lived single-active-attempt claim이며 same-attempt retry만 idempotent하다. Raw context는 current authorization을 재검증한 claim/materialization 시점에만 provider adapter에 제공한다.
- Subworkflow는 parent가 전달한 bounded context/channel만 사용하고 독립 session을 암묵적으로 생성하거나 parent session table을 조회하지 않는다.

다음 항목은 위 보안·무결성 baseline을 변경하지 않는 제품/운영 설정이며 구현 이슈에서 값과 UX를 확정한다.

- conversational input/output mapping schema와 Builder UX
- surface별 retention 수치, legal hold와 backup purge 운영 절차
- conversation write 실패의 사용자-facing degraded UX
- legacy chatbot guided migration 기간과 owner
- provenance persistence의 normalized relation/JSONB 세부 shape와 encryption key 운영
- Agent Builder/Chatbot의 default node Memory 제안 UX
- summary rebuild trigger와 policy/model version 변경 UX
- 명시적 cross-deployment conversation migration 지원 여부

## Current Implementation Gap

현재 코드는 위 target 요구사항을 충족하지 않는다. 특히 전역 memory flag, Chatbot 강제 ON, client conversation UUID, execution log 기반 read, node별 직접 summary 호출과 provenance/usage/revocation 부재가 남아 있다. 이 문서는 migration 완료 상태를 주장하지 않으며 구현 단계별 현재 상태는 관련 feature 문서와 git history에서 갱신한다.
