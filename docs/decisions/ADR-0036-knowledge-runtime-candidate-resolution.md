# ADR-0036: Knowledge runtime candidate resolution 경계

Status: Accepted

Related ADRs: [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md), [ADR-0020](ADR-0020-knowledge-mcp-incremental-sync-boundary.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0034](ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md)

## Context

MBA-231은 KB object/property RBAC, Collection action, Knowledge domain 위임,
source authorization provenance를 하나의 관리 경계로 정리했다. 다음 단계는
Workflow LLM node가 직접 선택한 KB와 선택한 Collection의 하위 KB를 실행 시점
주체 기준으로 다시 해석하는 것이다.

현재 Gateway의 `KnowledgeCandidateResolver`는 Builder 추천과 deployment preview를
위한 safe metadata resolver다. Collection scope가 생략되면 route 가능한 Collection
subset 또는 직접 권한이 있는 KB를 탐색하는 Builder 편의 동작이 있고, Gateway
SQLAlchemy service와 response schema에 결합되어 있다. 이를 Workflow Engine에서
가져다 쓰면 Builder 정책이 runtime authorization으로 승격되고 Gateway concrete
구현을 Worker가 역으로 import하게 된다.

또한 Collection, membership, KB lifecycle/readiness, Team/User permission, source
authorization provenance를 여러 query로 읽는 동안 PostgreSQL 기본 `READ COMMITTED`
transaction에서는 서로 다른 commit 시점이 섞일 수 있다. Source-managed KB의 live
connector authorization과 public exposure approval은 목표 계약에는 있지만 현재
MBA-232에서 사용할 완성된 runtime primitive가 아니다.

## Options

1. Gateway resolver를 Workflow Engine에서 직접 import한다.
2. Gateway resolver 전체를 shared service로 이동해 Builder와 runtime이 같은 동작을
   사용한다.
3. Shared에는 순수 contract와 결정적 merge policy만 두고, Workflow Engine에
   runtime application use case, port, PostgreSQL adapter와 composition을 둔다.

## Decision

선택지 3을 채택한다.

### Runtime과 Builder resolver 분리

- Gateway `KnowledgeCandidateResolver`는 Builder/preflight safe candidate resolver로
  유지한다.
- MBA-232 runtime resolver는 Workflow Engine `runtime_retrieval` application
  boundary가 소유한다.
- Shared package에는 FastAPI, Celery, SQLAlchemy, Gateway, Workflow Engine concrete
  구현을 모르는 immutable contract와 pure ordering/budget policy만 둔다.
- API, Workflow graph, Builder/preflight adapter, LLM node와 retrieval 연결은
  MBA-233 범위다. MBA-232 composition은 연결 가능한 seam까지만 제공한다.

### Server-owned request와 audience

Runtime request는 canonical `organization_id`, direct KB ID 목록, 명시적으로 선택한
Collection ID 목록, server candidate budget과 다음 두 audience 중 하나를 가진다.

- `AuthenticatedAudience`: canonical organization과 현재 execution user
- `AnonymousPublicAudience`: canonical organization만 보유하며 synthetic subject를
  만들지 않음

Optional user 값에서 workflow owner, builder, deployment owner, credential principal,
KB creator 또는 service account로 fallback하는 동작은 금지한다. Service account와
operator audience는 별도 lifecycle/approval 결정 전까지 이 union에 추가하지 않는다.

Runtime은 명시한 Collection만 해석한다. Collection ID가 생략되거나 빈 목록이면
Collection stream은 0개다. Organization 전체 Collection 탐색, query-aware routing,
semantic discovery는 수행하지 않는다.

### Authenticated authorization

- Direct KB는 active organization, KB active lifecycle, retrieval readiness, KB `use`,
  source-managed인 경우 materialized source authorization gate를 통과해야 한다.
  Collection `route`는 요구하지 않는다.
- Collection child는 Collection active lifecycle과 `route`, membership 존재, child KB
  active lifecycle/readiness, child KB `use`, source-managed인 경우 materialized source
  authorization을 모두 통과해야 한다.
- Collection `read/manage/sync`와 Knowledge domain `catalog_manage`,
  `permission_delegate`, `lifecycle_manage`, `sync_manage`는 runtime `route/use`를
  대체하지 않는다.

### Anonymous public-only authorization

- Selected Collection은 active이고 `safe_metadata["visibility"] == "public"`이어야
  한다. 누락, 다른 값, malformed 값은 private로 취급한다.
- Direct KB도 active public Collection에 현재 연결되어 있어야 한다.
- Manual active/ready KB만 public candidate가 될 수 있다.
- Source-managed Collection은 별도 source/connector public exposure approval store가 현재
  구현되어 있지 않으므로 public visibility와 manual child KB가 있어도 fail-closed
  제외한다. Collection source identity projection이 누락된 상태도 허용으로 해석하지
  않는다.
- Source-managed KB는 별도 source/connector public exposure approval store가 현재
  구현되어 있지 않으므로 public Collection에 연결되어 있어도 fail-closed 제외한다.
- Anonymous path는 Team/User Collection/KB permission, domain permission, 로그인
  cookie의 user를 사용하지 않는다.

### Membership, lifecycle와 readiness

`KnowledgeCollectionItem`은 lifecycle column을 갖지 않는다. Row가 존재하면 현재
membership이고 unlink 또는 missing이면 candidate가 아니다. Collection lifecycle과
child KB lifecycle은 독립적으로 평가한다.

KB는 active lifecycle이고 `sync_state != "source_deleted"`여야 한다. 기본 readiness는
active `DocumentVersion.status == "ready"`다. 전환기에는 completed document에 연결된
unversioned chunk가 있고 `KnowledgeBase.active_document_version_id IS NULL`인 legacy
KB만 현재 retrieval-visible 계약에 따라 허용할 수 있다. Active pointer가 non-ready
version을 가리키는 동안에는 unversioned chunk로 fallback하지 않는다. Pre-finalized
chunk, non-ready version 또는 document row 존재만으로 ready를 추정하지 않는다.

### Materialized source authorization

MBA-232는 `SourceAuthorizationProvenance`에 materialize된 현재 DB fact만 사용한다.
Organization, KB/source identity, authenticated requester subject, active status,
freshness/expiry, requester authorization과 저장된 source action이 일치해야 한다.
Missing, inactive, stale, expired, unmapped, ambiguous, unverified, revoked, denied,
unknown, mismatched fact는 해당 KB를 제외한다.

Runtime retrieval과 호환되는 materialized source action은 공백과 대소문자를
정규화한 `read`, `view`, `use`, `retrieve`, `search`다. `NULL`, 빈 값, 쓰기·관리
동작 또는 알 수 없는 동작은 retrieval 승인을 의미한다고 추론하지 않고
`source_authorization.operation_unverified`로 제외한다. Provider별 action을 이
집합으로 매핑하는 책임은 provenance 생산 경계에 있다.

MBA-232 resolver는 connector client, `check_access_batch`, single `check_access`, HTTP
client 또는 runtime source-authorization cache를 호출하거나 구현하지 않는다. Live
재확인과 short-lived cache는 별도 source authorization 이슈에서 결정한다. Candidate
ID 또는 permission 결과도 invocation 사이에 cache하지 않는다.

### Deterministic merge와 budget

초기 server budget은 최대 20 unique KB다.

1. 허용된 direct KB를 graph/configured order로 먼저 추가한다.
2. 남은 budget은 selected Collection configured order 기준 round-robin으로 채운다.
3. Collection 내부는 item rank, item creation time, KB UUID 순서를 사용한다.
4. Canonical KB UUID로 dedupe하고 처음 허용된 provenance를 보존한다.
5. Duplicate를 만나면 해당 slot을 소비하지 않고 traversal을 계속한다.

Adapter의 membership scan도 선택한 첫 Collection이 나머지를 굶기지 않도록 fair하고
bounded해야 한다. Global window 뒤에만 `LIMIT`을 두지 않고, configured Collection
`VALUES` relation과 ordered LATERAL subquery로 각 Collection을 `scan_cap + 1` 이하로
먼저 제한한 뒤 bounded intermediate relation에만 round-robin window를 적용한다. 이
구현은 기존 ordering을 유지하고 DB migration 없이 window 입력을 최대
`selected_collection_count * (scan_cap + 1)`로 제한한다. Budget 또는 scan cap 도달은
성공 결과에 fixed safe warning과 bucketed summary를 붙이는 동작이며 authorization
partial failure가 아니다.

### PostgreSQL invocation snapshot

Resolver adapter는 invocation마다 fresh SQLAlchemy session/transaction을 열고 첫
query 전에 PostgreSQL `REPEATABLE READ`와 transaction read-only를 적용한다. 단순
`READ ONLY`는 기본 `READ COMMITTED`에서 여러 statement의 snapshot을 고정하지 못한다.
Session-wide isolation 변경을 pooled connection에 남기지 않는다.

Collection, membership, lifecycle/readiness, permission, materialized source
provenance query는 같은 snapshot을 사용한다. 중간에 commit된 변경은 다음 resolver
invocation부터 반영한다. Fake session test는 이 동시성 계약의 증명이 아니며 disposable
PostgreSQL two-transaction test를 둔다. Source-policy grant와 materialized provenance의
expiry는 KB마다 wall clock을 다시 읽지 않고 같은 transaction에서 한 번 읽은 timezone-aware
`transaction_timestamp()`를 permission helper에 주입해 invocation 전체에서 재사용한다.

Disposable PostgreSQL evidence는 membership뿐 아니라 Collection `route`, KB `use`,
organization membership, source provenance, Collection lifecycle와 KB lifecycle 변경을
포함한다. 관련 runtime resolver 경로가 바뀌는 pull request와 `dev` push에서 전용
path-scoped workflow가 이 evidence를 실행한다.

### Result와 failure

정책상 hidden, cross-organization, inactive, unlinked, not-ready, denied, stale,
unknown 후보는 identity를 노출하지 않고 제외한다. 후보가 0개면 provider/retrieval을
호출하지 않는 `safe_no_result`를 반환한다.

DB session, snapshot, repository 또는 authorization helper infrastructure가 실패하면
이미 평가한 후보를 부분 결과로 반환하지 않는다. Resolver 전체를 fixed safe code의
retryable infrastructure failure로 종료하고 retrieval/provider 호출 전에 닫는다. 일부
authorized KB의 실제 retrieval timeout/partial result는 MBA-233 또는 기존 Retrieval
Orchestrator의 downstream 정책이다.

Durable observability에는 routing mode, configured/authorized count bucket,
budget-limited boolean, fixed reason/warning, safe latency/correlation만 허용한다. Raw
query/graph/payload/content, source title/path/URL/principal/ACL, permission row, hidden
KB/Collection ID/name, exact denied count, credential, prompt/completion은 저장하지 않는다.

## Consequences

장점:

- Builder convenience policy가 runtime authorization으로 승격되지 않는다.
- Workflow Engine이 Gateway concrete package를 import하지 않는다.
- Current execution audience, route/use/source gate와 anonymous public-only behavior를
  한 테스트 가능한 경계에서 강제한다.
- Direct KB와 여러 Collection을 deterministic하고 bounded하게 함께 사용할 수 있다.
- 한 invocation 안에서 permission/membership revision이 섞이는 것을 막는다.

비용:

- Gateway Builder resolver와 runtime resolver가 목적상 공존하며 MBA-233에서 명시적
  adapter/contract mapping이 필요하다.
- Resolver마다 fresh repeatable-read transaction을 열어야 한다.
- Live source authorization이 연결되기 전에는 materialized provenance freshness에
  의존하고 source-managed anonymous candidate는 항상 제외된다.

## Follow-up

- MBA-232는 pure policy, Workflow Engine application/port, PostgreSQL adapter,
  composition과 policy/DB/concurrency test를 구현한다.
- MBA-233은 additive Workflow graph field, Builder selector, deployment preflight,
  LLM node와 bounded retrieval wiring을 구현한다.
- Source live authorization, public exposure store, service account/operator audience,
  query-aware organization-wide routing은 각각 별도 결정과 이슈가 필요하다.
