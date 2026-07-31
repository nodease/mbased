# ADR-0039: Workflow Knowledge Collection 라우팅 통합

Status: Accepted

Related ADRs: [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md), [ADR-0019](ADR-0019-agent-builder-preview-apply-save-boundary.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0034](ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md), [ADR-0036](ADR-0036-knowledge-runtime-candidate-resolution.md)

## Context

MBA-232는 direct Knowledge Base와 명시 selected Knowledge Collection을 current
execution audience 기준으로 해석하는 pure policy, Workflow Engine application
use case/port, PostgreSQL `REPEATABLE READ, READ ONLY` adapter와 composition을
구현했다. 그러나 현재 LLM node graph는 `knowledgeBases`만 저장하고, Builder,
Gateway save/preflight, LLM runtime과 retrieval fan-out은 MBA-232 resolver를 호출하지
않는다.

기존 Worker는 모르는 Pydantic field를 무시할 수 있다. 따라서 Client가
`knowledgeCollections`를 먼저 저장하면 구 Worker가 Collection-only LLM node를
Knowledge 비활성 node로 실행하고 근거 없는 provider 호출을 할 수 있다. 또한
Collection 관리 목록의 `read/manage` projection을 picker에 재사용하거나 저장된 label,
Client capability, preflight 결과를 실행 권한으로 사용하면 Collection `route`, child KB
`use`, source authorization 책임이 섞인다.

## Options

1. 저장 시 Collection child를 `knowledgeBases`로 정적 확장한다.
2. Gateway Builder candidate resolver를 Worker에서 호출하거나 import한다.
3. Graph에는 direct KB와 selected Collection intent를 별도로 저장하고 Builder/save,
   preflight, runtime마다 자기 audience와 책임에 맞는 검증을 수행한다.

## Decision

선택지 3을 채택한다.

### Additive graph contract

- LLM node data는 기존 `knowledgeBases`와 새 `knowledgeCollections`를 함께 가질 수
  있다. 두 목록은 상호 배타적인 mode가 아니다.
- `knowledgeBases` item은 canonical UUID `id`와 legacy display snapshot `name`,
  `knowledgeCollections` item은 canonical UUID `id`와 optional `safeLabel`만 가진다.
- 각 목록의 최대 configured reference는 20개다. 21번째 reference, malformed object,
  non-canonical UUID, 허용되지 않은 field와 control character가 있는 display 값은
  저장·실행·배포 전에 거부하고 silent slicing이나 자동 삭제를 하지 않는다.
- Display snapshot은 최대 255자이고 UI round-trip에만 사용한다. Permission, source,
  routing, audit와 trace 판단은 server-loaded UUID resource만 사용한다.
- Collection membership은 graph에 materialize하지 않는다. 기존 `workflows.graph`와
  `workflow_deployments.graph_snapshot` JSONB 안의 additive field이므로 MBA-233에서 DB
  migration이나 `llm_node_versions` column을 추가하지 않는다.

### Builder picker와 save authorization

- Direct KB picker는 기존 active-organization, active lifecycle,
  `sync_state != source_deleted`, effective KB `use`, source gate와
  retrieval-selectable 조건을 통과한 `/knowledge/llm-selectable`을 사용한다.
- Collection picker는 별도 authenticated endpoint를 사용하고, active organization의
  active lifecycle이며 `sync_state != source_deleted`인 Collection 중 current editor가
  Collection `route`를 가진 항목만 반환한다.
  Response는 UUID와 display-policy-approved optional safe label만 허용한다.
- Collection 관리 목록의 `read/manage/sync`, Knowledge domain 관리 action, raw name,
  description, child ID/count, source metadata와 permission row를 picker authority나
  response로 사용하지 않는다.
- 모든 editable graph persistence path는 structural validation 뒤 current editor 기준
  direct KB effective `use`와 selected Collection `route`를 다시 확인한다. Direct
  source-managed KB는 기존 source authorization gate도 통과해야 한다. Direct KB와
  selected Collection은 active lifecycle이고 `sync_state != source_deleted`여야 하며,
  direct KB는 retrieval-visible completed chunk를 가져야 한다.
- Save authorization은 Collection child를 열거하거나 child KB/source permission을
  평가하지 않는다. Child authorization은 invocation-time MBA-232 resolver가 소유한다.
- Graph와 success audit write는 기존 transaction 안에서 원자적으로 처리한다. 권한
  read 뒤 동시 revoke가 commit되어 configuration intent가 남을 수 있어도 저장 결과를
  runtime capability나 lease로 재사용하지 않고 다음 invocation에서 다시 평가한다.
- 저장된 reference가 picker에서 사라지면 Client는 순서와 reference를 자동 삭제하지
  않고 generic unavailable 상태로 표시한다. 새 저장은 해당 reference를 제거하거나
  권한이 복구될 때까지 generic error로 차단한다.

### Deployment preflight

- Preflight는 두 목록의 shape/limit와 selected resource의 organization/lifecycle/sync,
  direct KB retrieval-visible readiness, deployment type에서 server-derived한 audience
  정책을 검증한다.
- Anonymous surface는 private Collection/KB와 public exposure primitive가 없는
  source-managed content를 active deployment에 올리지 못한다.
- `workflow_node_inherited`는 owner나 credential principal로 대체하지 않고 inherited
  subject warning을 반환한다.
- Preflight는 child ID, hidden identity, exact denied count를 반환하거나 runtime
  capability를 발급하지 않는다. Candidate budget 초과 가능성은 bucket과
  `candidate_budget_limited`의 보수적 warning으로만 표현한다.
- Runtime은 preflight 성공 여부와 무관하게 invocation 시점에 다시 resolve한다.

### Workflow runtime wiring

- Direct-only, Collection-only, mixed Knowledge configuration은 모두 MBA-232
  `KnowledgeRuntimeCandidateResolver`를 LLM invocation마다 정확히 한 번 호출한다.
- Execution audience는 server-verified organization과 explicit user
  `execution_subject` 또는 `AnonymousPublicAudience`로만 만든다. Workflow owner,
  builder, deployment owner, app creator, credential principal과 `user_id`를 Knowledge
  audience로 fallback하지 않는다.
- Resolver가 반환한 ordered canonical KB ID만 기존 bounded retrieval fan-out에
  전달한다. Direct와 여러 Collection에서 중복된 KB는 한 번만 검색한다.
- `source_deleted` parent Collection은 route permission이나 남아 있는 membership과
  무관하게 invocation snapshot에서 제외한다.
- Policy상 candidate 0개는 retrieval/embedding/provider를 호출하지 않는 safe
  no-result다. Resolver infrastructure failure는 `ragFailurePolicy`로 정상 empty
  result로 낮추지 않고 raw exception 없는 retryable workflow failure로 전파한다.
- Resolver 이후 일부 authorized KB retrieval timeout은 기존 bounded partial-result와
  evidence sufficiency policy가 처리한다. 근거가 충분하지 않으면 ungrounded provider
  호출을 하지 않는다.
- LLM node는 Collection membership, Collection permission과 child KB permission SQL을
  직접 실행하지 않는다. MBA-232 adapter가 유일한 runtime candidate authorization
  경계다.

### Graph consumers and projection

- Agent Builder, cost optimizer, model-routing refresh, compare/copy/import와 deployment
  snapshot은 operation이 Collection selection을 명시적으로 편집하지 않는 한
  `knowledgeCollections`를 그대로 보존한다.
- Agent Builder의 현재 recommendation은 direct KB만 materialize하며 Collection을
  자동 선택하지 않는다.
- Pre-execution Knowledge sync는 explicit `knowledgeBases`만 처리한다. Collection
  children을 미리 확장하거나 live connector를 호출하지 않는다.
- Public app/deployment graph projection은 `knowledgeBases`와
  `knowledgeCollections`를 모두 제거한다.

### Observability and failure projection

- Durable trace/log/audit에는 routing mode, configured/selected count bucket,
  budget/scan limited flag, partial/failure bucket, fixed reason code와 latency만 추가할
  수 있다.
- Collection ID/name, provenance Collection ID, hidden KB ID, child list/count, raw graph,
  query, source path/URL/title/ACL, credential, prompt/completion과 provider raw payload를
  새로 저장하거나 response/SSE error에 포함하지 않는다.
- Policy exclusion은 hidden resource별 denial audit을 만들지 않는다. 기존 authorized
  KB retrieval audit과 generic request-scoped policy/audit boundary는 유지한다.

### Rollout and rollback

- 새 graph field를 노출하기 전에 모든 Workflow Worker를 MBA-233 code로 배포하고 구
  task를 drain한다. 그 다음 Gateway save/preflight를 배포하고 Client selector를
  마지막에 노출한다.
- Rollback은 Client 노출 중지, Gateway write 중지, queue drain, Worker rollback의
  역순이다. Collection graph를 구 Worker가 소비할 수 있는 동안에는 rollback을
  완료한 것으로 보지 않는다.
- 현재 일반 Worker capability registry가 없으므로 자동 version negotiation을
  구현됐다고 주장하지 않는다. 기존 direct-only graph는 additive field 부재를 빈
  Collection 목록으로 해석해 호환한다.

## Consequences

장점:

- 사용자는 direct KB와 관리자가 묶은 Collection을 같은 LLM node에서 명시적으로
  조합할 수 있다.
- Builder visibility, save authorization, deployment preflight와 runtime authorization이
  서로 capability를 재사용하지 않는다.
- Dynamic Collection membership과 permission 변경이 graph rewrite 없이 다음
  invocation에 반영된다.
- 구 graph는 유지하면서 silent truncation과 구 Worker의 ungrounded 실행을 차단한다.

비용:

- Shared, Gateway, Client, Workflow Engine과 deployment preflight의 graph consumer를
  모두 분류하고 테스트해야 한다.
- Worker-first drain이 필요하며 현재는 자동 capability negotiation이 없다.
- Invocation마다 MBA-232 snapshot query 비용이 발생한다.

## Follow-up

- Query-aware organization-wide Collection discovery와 Agent Builder Collection 자동
  추천은 별도 이슈다.
- Service account/operator Knowledge audience, live source authorization/cache와 source
  public exposure store는 각각 별도 결정이 필요하다.
- Hierarchical chunking, reranking, query rewrite와 retrieval quality tuning은 이 ADR의
  graph/routing 통합 범위가 아니다.
