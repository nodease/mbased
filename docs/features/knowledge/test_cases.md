# Knowledge Test Cases

Status: Draft
Verified Against: feature/mba-354 @ 1bf745609a6ea85574bd49219e29e83c1ace2819
이 문서는 현재 RAG 동작과 목표 KB 통합 모델에 필요한 테스트 범위를 함께 기록한다. MBA-105 목표 모델 테스트는 [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)과 [implementation_baseline.md](implementation_baseline.md)의 임시 baseline을 기준으로 구현 blocker가 된다.
KC sync의 실행·복구·snapshot·versioned finalization 검증은 [ADR-0048](../../decisions/ADR-0048-knowledge-collection-sync-execution-boundary.md)을 따른다.
Organization Detector Provider와 embedding 전 local masking Target 테스트는 [ADR-0070](../../decisions/ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)을 따른다. MBA-362가 runtime/persistence/provider adapter 테스트를 TDD로 구현하기 전에는 완료 증거가 아니다.

## Unit Tests

- 새 LLM node, Agent Builder LLM node 초안, Knowledge RAG 추천 옵션과 runtime missing-value fallback은 `scoreThreshold=0.3`, `topK=5`를 사용하고, 명시된 기존 값은 덮어쓰지 않는다.
- Workflow LLM node에 `context_variable`이 설정되면 RAG embedding/retrieval query는 렌더링된 user prompt 전체가 아니라 해당 `referenced_variables` 값만 사용한다. 값이 없거나 정제 뒤 비면 embedding과 retrieval을 호출하지 않는 safe no-evidence 경로로 닫고, `context_variable`이 없는 legacy graph는 기존 렌더링 prompt query를 유지한다.
- Opt-in CrossEncoder rerank는 authorized chunk의 암호문이 아니라 메모리에서 복호화한 redacted canonical text를 입력으로 사용한다. 복호화 실패 시 암호문을 model input으로 fallback하거나 response, trace, audit, log에 남기면 실패다.
- Metadata filter는 allowlist된 key/operator만 허용하고 free-form dict, JSONPath, raw SQL fragment, secret/header/prompt/completion/raw response field를 거부한다.
- Classification metadata가 없으면 [ADR-0007](../../decisions/ADR-0007-mvp2-classification-metadata-storage.md)에 따라 `internal`로 처리한다.
- 목표 cutover 전 `document_chunks.metadata`와 현재 `documents.meta_info`가 충돌하면 document metadata를 우선한다.
- 목표 metadata sanitizer는 protected source identity field를 public metadata/filter path에서 거부한다.
- Redaction은 기본적으로 chunk content, embedding input, retrieval-visible artifact에 사용되는 canonical text를 만든다.
- Raw source content를 저장하더라도 protected raw artifact store 또는 encrypted object storage metadata table에만 저장하고 `document_chunks.content`에는 저장하지 않는다.
- Raw artifact storage path는 organization/source opt-in, raw/compliance gate, encryption, retention, legal hold, purge, audit policy가 명시적으로 활성화되지 않으면 비활성 상태다.
- Privacy/Redaction baseline은 admin policy가 비활성화할 수 없고, organization/collection/source/KB policy는 더 엄격하게 조정하거나 승인된 display mode만 선택할 수 있다.
- Knowledge Skill body/resource는 raw source content, raw source title/path/url, raw principal, raw ACL fact, restricted document list, hidden KB id, raw prompt/completion/provider response를 포함하지 않는다.
- Skill metadata sanitizer는 skill name/description/tag/source tier/owner/freshness도 민감 metadata로 보고 display-policy-approved safe field만 허용한다.
- Stale/review-required/deprecated skill은 운영 workflow 생성 자동 후보나 실행 시점 RAG procedure에서 fail-closed 또는 remediation surface로 제한된다.
- Golden question/eval fixture는 raw restricted content를 포함하지 않고 safe reference와 expected behavior만 사용한다.
- Source public ACL은 기본적으로 organization-wide KB read/use로 materialize되지 않는다.
- Source ACL provenance storage는 raw source permission, source permission action/provenance, source authorization state, KB permission `auth_state`를 섞어 저장하지 않는다.
- Source ACL authorization만으로 KB `use`가 충족됐다고 판정하지 않는다.
- Auto-ingested KB는 admin/team/user grant 또는 organization-approved connector/source policy가 명시 KB `use`를 provision하지 않으면 retrieval 후보가 되지 않는다.
- Runtime access cache key는 source item 또는 document version 단위를 포함한다. Subject-level `allowed` cache가 다른 source item에 재사용되면 테스트 실패다.
- Redaction 전 ephemeral content handle은 process/run scope와 short TTL을 가지며 durable DB, retry/dead-letter payload, audit, trace, log, user-facing response에 handle value나 raw content가 남으면 테스트 실패다.
- Ingestion lock release는 owner token을 비교한다. TTL 만료 뒤 다른 worker가 lock을 획득한 경우 stale worker는 새 worker의 lock을 삭제하지 못한다.
- Fencing token 또는 동등한 guard가 없는 stale worker는 active version, `content_hash`, chunking fingerprint, external index namespace를 finalize하지 못한다.

## Permission And RBAC Tests

- External parser 재활성화 이후에도 LlamaParse document processing은 execution subject와 active organization이 모두 있는 경우에만 시작한다. 다른 user/organization credential, revoke/invalid credential, provider 불일치, `use` 권한 상실, 후보 없음 또는 복수 후보에서는 provider parser 호출이 발생하면 실패다. 현재 미지원 상태에서는 이 검증보다 먼저 `knowledge.raw_parser_egress_unavailable`로 닫혀야 한다.
- External parser 재활성화 시 parser input resolver는 기존 credential service boundary를 사용한다. FileProcessor가 `LLMCredential` row 또는 stored config를 직접 조회/해석하거나 created_at 최신 row를 fallback으로 선택하면 실패다. 현재 미지원 상태에서는 resolver 호출 자체가 0회다.
- LlamaParse parser credential 실패 또는 현재 미지원 failure의 processing metadata, API error, audit/trace/log capture와 fixture에는 credential ID, API key, config 원문, decrypted value와 provider raw payload가 없어야 한다.
- Team onboarding demo seed는 플랫폼개발·영업·재무 팀 전용 빈 KB를 해당 팀과 People 팀에만 부여한다. 플랫폼개발팀 사용자의 runtime 후보에 영업·재무 KB가 포함되거나 영업팀 사용자의 후보에 플랫폼개발·재무 KB가 포함되면 실패한다.
- Team onboarding demo의 세 KB는 `demodata/` PDF를 Document로 등록한다. runtime OpenAI credential 옵션만 사용해도 기존 precomputed fixture가 법령·사내문서를 채우고, PDF를 실제 파싱한 `text-embedding-3-small` embedding을 생성해 reset 직후 검색 가능해야 한다. 이 경로는 로컬 법령 PDF 원본을 요구하지 않는다. 플랫폼 PDF의 manager-only 마지막 페이지는 chunk ACL을 가장하지 않고 일반 플랫폼 KB 복사본에서 제외한다.
- Demo reset은 demo Knowledge Base 또는 demo 사용자와 연결된 `user_knowledge_permissions`를 Knowledge Base보다 먼저 삭제해야 한다. migration backfill이나 시연 중 생성된 직접 권한이 남아 있어도 외래키 오류 없이 reset 후 같은 demo 상태를 재생성해야 한다.
- Demo reset은 demo Knowledge Base와 연결된 `knowledge_ingestion_outbox`만 Knowledge Base보다 먼저 삭제하고, 곧바로 upsert할 demo 조직 row 자체는 삭제하지 않는다. 같은 조직의 사용자 생성 Knowledge Base에 연결된 outbox 작업은 보존해야 하며, demo KB의 오래된 outbox 작업은 남지 않아야 한다.
- 현재 demo seed는 document-level KB 권한만 보장한다. 같은 PDF 안의 chunk별 동적 `role_acl`을 권한 경계로 주장하지 않으며 manager-only 내용은 별도 KB로 분리해야 한다.

- Document progress SSE는 stream을 열기 전에 active organization과 KB `read`를 검증한다. 권한 없는 actor나 다른 organization context는 document status, safe error, Redis progress를 한 건도 수신하지 못한다.
- KB hard delete는 Organization manager와 acknowledgement가 있어도 approved retention/legal-hold checker가 없으면 storage/DB mutation 전에 fail-closed한다. 삭제 mechanics transaction 테스트는 explicit allow checker를 주입하며 production eligibility 증거로 취급하지 않는다.
- Collection `read`, `route`, `manage`, `sync`만으로는 하위 KB content retrieval 권한이 생기지 않는다.
- Auto collection mode는 route 권한이 없는 collection scope에서 유래한 KB를 제외한다.
- Explicit KB mode는 collection route permission을 생략할 수 있지만 KB helper allow, source ACL gate, final evidence policy는 계속 요구한다.
- 현재 retrieval/search-test content access에는 KB `use`가 필요하며 read/listing permission만으로는 content retrieval이 되지 않는다.
- Source-managed KB는 mbased KB `use`와 fresh requester source ACL authorization을 모두 요구한다.
- 빌더 단계 skill visibility만으로 실행 시점 collection route, KB `use`, source ACL gate가 충족되지 않는다.
- Skill이 특정 KB/collection을 routing hint로 제안하더라도 실행 시점 permission helper가 거부한 KB는 workflow 실행 후보에서 제외된다.
- Manual KB grant는 stale/unmapped/ambiguous/unverified source ACL freshness gate를 우회하지 못한다.
- Source ACL revocation은 이후 retrieval을 막고 freshness epoch/cache invalidation signal을 갱신한다.
- Organization manager는 operations policy에 따라 remediation을 수행할 수 있지만 기본적으로 source ACL retrieval filtering을 우회하지 못한다.
- 다른 organization KB id의 response shape, audit behavior, answer-run 생성 여부는 ADR-0017 resource-hiding baseline에 따라 hidden identity를 만들지 않는다.
- Visible resource 확인 이후 same-scope KB use denial은 승인된 resource-hiding/API matrix를 따른다. Matrix가 resource visible 상태를 유지한다고 결정한 경우에만 `403 permission.denied`를 허용한다.
- Active organization member라는 사실만으로 KB `use`가 허용되지 않는다. Team 또는 user direct KB grant가 없고 organization manager override도 없으면 retrieval은 fail-closed다.
- `user_knowledge_permissions` direct grant는 같은 active organization member에게만 생성된다. invited/suspended/removed/non-member 대상은 safe validation 또는 hidden/not-found response로 닫는다.
- User direct KB grant와 team KB grant가 함께 있으면 가장 강한 additive allow가 effective permission이 된다. User direct grant가 team grant를 낮추거나 deny할 수 없다.
- User direct KB grant request에서 `none`은 거부한다. 권한 회수는 DELETE endpoint로만 표현한다.
- User direct KB grant가 있어도 source-managed KB retrieval은 fresh source ACL/requester authorization gate를 다시 통과해야 한다.
- MBA-231의 모든 KB-bearing API는 `read/use/write/content_read/manage` 중 하나로 분류되고 active organization scope를 먼저 고정한다. Owner attribution만으로 action이 허용되거나 Team/User grant가 owner predicate 때문에 거부되면 테스트 실패다.
- `content_read`는 builder 이상에 매핑되지만 source-managed original content는 requester source authorization과 approved display/raw policy가 없으면 Organization manager에게도 fail-closed된다.
- Active member manual KB create는 KB, creator user-direct `manager`, canonical audit를 한 transaction에서 생성한다. Grant 또는 audit flush/commit 실패는 세 row를 모두 rollback한다.
- Legacy owner backfill은 같은 organization active member만 grant하고 stronger grant를 낮추지 않으며 반복 실행해도 중복 row/audit을 만들지 않는다. Cross-org/inactive/non-member/ambiguous owner는 identity나 KB label 없이 safe finding bucket으로 남긴다.
- Team/User Knowledge domain grant는 Organization manager만 변경할 수 있고, action allowlist, same-org active subject, optional expiry와 non-negative flags constraint를 강제한다.
- Knowledge domain grant는 inactive Team, deactivated/removed User, non-member 또는 cross-organization subject를 계속 거부한다. Revoke는 같은 대상의 기존 permission row가 있으면 subject active check 없이 row를 lock/delete하고 audit와 함께 commit한다. Existing row가 없는 revoke는 idempotent하며 audit를 만들지 않는다.
- Inactive/removed subject의 domain permission revoke에서 audit 저장 또는 commit이 실패하면 permission delete도 rollback된다. 다른 organization의 동일 subject/action row는 조회·삭제·audit되지 않는다.
- Domain revoke repository test는 filter를 무시하는 query fake를 사용하지 않는다. PostgreSQL dialect로 organization/subject/action bind predicate와 `FOR UPDATE`를 compile 검증하고, opt-in disposable PostgreSQL test는 cross-org row 보존, audit failure rollback, 두 session의 concurrent revoke가 `deleted` 1회와 `unchanged` 1회 및 delete audit 1개만 만드는지 검증한다.
- Expired domain grant는 cleanup worker 실행 여부와 무관하게 effective action에서 제외된다. Team과 user direct domain grant는 additive allow이며 explicit deny를 만들지 않는다.
- Domain action은 KB read/use/content, Collection route를 상속하지 않는다. `catalog_manage`만 가진 actor의 RAG 검색과 원문 조회가 허용되면 테스트 실패다.
- `permission_delegate` actor가 자신 또는 자신이 active member인 Team에 content-plane grant를 시도하면 mutation 없이 safe policy block audit만 정확히 한 번 기록한다. Organization manager와 resource manager의 기존 recovery path는 별도 positive case로 검증한다.
- Collection role bundle은 Viewer=`read`, Workflow Router=`read+route`, Maintainer=`read+manage`, Sync Operator=`read+sync` explicit row를 한 transaction에서 적용한다. 일부 row 또는 audit 저장 실패 시 bundle 전체를 rollback하고 KB `use` row를 만들지 않는다.
- Domain `catalog_manage` actor는 private manual Collection과 routing membership을 관리할 수 있지만 public membership 변경은 Organization manager acknowledgement 없이는 차단된다. 이 actor의 link/reorder는 Collection privacy binding 또는 Organization validity epoch을 만들거나 바꾸지 않는다. Source public exposure primitive가 없으면 source-managed KB의 public link/visibility 전환은 `source_public_exposure_required`로 fail-closed된다.

## MBA-232 Workflow Runtime Candidate Resolver Tests

- Shared runtime contract는 `AuthenticatedAudience(organization_id, user_id)`와 `AnonymousPublicAudience(organization_id)`만 허용하고 optional subject, owner, builder, deployment owner, credential principal, service account fallback을 표현하지 않는다.
- Runtime `collection_ids` missing/empty는 Collection stream 0개다. Gateway Builder resolver의 route-safe subset/direct-KB fallback을 호출하거나 organization 전체 Collection을 query하면 테스트 실패다.
- Direct KB는 Collection route 없이 KB `use`와 applicable materialized source gate를 통과할 수 있다. Collection child는 selected active Collection `route`와 독립적인 child KB `use`/source gate를 모두 통과해야 한다.
- Collection `read/manage/sync`와 Knowledge domain `catalog_manage`, `permission_delegate`, `lifecycle_manage`, `sync_manage`만 가진 actor는 runtime candidate를 얻지 못한다.
- Direct candidates는 configured order를 유지하고 남은 budget은 selected Collection configured order의 round-robin으로 채운다. Collection 내부 tie-break는 item rank, item created time, KB UUID다.
- Direct와 여러 Collection에 중복된 KB는 canonical KB UUID로 한 번만 반환하고 처음 허용된 provenance를 유지한다. Duplicate를 건너뛴 뒤 뒤쪽 unique KB로 budget을 계속 채운다.
- Candidate budget 19/20 boundary는 deterministic success이고 21 이상의 direct/Collection reference 또는 budget 20 초과 request는 configuration validation에서 silent truncation 없이 차단한다. Dynamic membership overflow는 fixed `candidate_budget_limited` safe warning을 반환한다.
- `KnowledgeCollectionItem`에는 lifecycle을 가정하지 않는다. Present row는 linked, unlink/missing은 후보 없음이며 Collection과 child KB lifecycle을 따로 검증한다.
- Active ready version 또는 documented completed-document unversioned legacy chunk fallback만 ready다. Archived/deleted/source_deleted/non-ready/pre-finalized 후보는 identity 없이 제외한다.
- Authenticated source-managed KB는 active/fresh/unexpired/matching materialized `SourceAuthorizationProvenance`가 필요하다. Missing/inactive/stale/unmapped/ambiguous/unverified/revoked/denied/unknown/expired/organization-requester-KB-source mismatch는 fail-closed다.
- MBA-232 adapter는 connector client, HTTP client, `check_access_batch`, single `check_access`, runtime source authorization cache를 0회 호출한다.
- Anonymous selected Collection child는 active public manual Collection의 active/ready manual KB만 허용한다. Direct manual KB도 하나 이상의 active public manual Collection membership이 필요하다. Source public exposure primitive가 없는 동안 source-managed Collection은 manual child만 포함해도 membership scan 전에 제외하고, source-managed KB는 public membership과 authenticated provenance가 있어도 모두 제외한다. Collection source identity field가 projection에서 누락되거나 malformed이면 anonymous path는 fail-closed다.
- PostgreSQL adapter는 fresh transaction의 첫 query 전에 `REPEATABLE READ, READ ONLY`를 적용한다. Path-scoped PostgreSQL CI의 two-transaction test에서 resolver 시작 뒤 membership/Collection route/KB use/organization membership/source provenance/Collection lifecycle/KB lifecycle 변경이 commit되어도 current invocation은 한 snapshot만 보고 다음 invocation이 변경을 본다.
- Source-policy/provenance expiry는 timezone-aware PostgreSQL transaction timestamp 하나로 전체 invocation을 평가한다. KB 순회 중 wall clock이 만료 경계를 지나도 같은 invocation에서 서로 다른 evaluation time을 사용하면 테스트 실패다.
- Snapshot/repository/authorization infrastructure exception은 fixed safe retryable whole-resolution failure다. 이미 평가한 candidate partial set, raw SQL/exception, identifier, source metadata, exact count를 반환하거나 retrieval/provider mock을 호출하면 테스트 실패다.
- Candidate 0개는 `safe_no_result`, budget 제한은 successful warning이며 downstream partial retrieval failure와 구분한다.
- Query count는 candidate/Collection 수에 비례하는 N+1이 아니고 selected 20 Collections/5,000 membership fixture에서도 scan/memory/result가 bounded하고 fair해야 한다. Membership SQL은 Collection별 LATERAL cap을 global window보다 먼저 적용하고 outer `LIMIT`만으로 boundedness를 주장하지 않는다.
- Shared pure policy는 SQLAlchemy/FastAPI/Celery/Gateway/Workflow Engine concrete package를 import하지 않고 Workflow Engine runtime retrieval production code는 `apps.gateway.*`를 import하지 않는다.

## MBA-233 Workflow Collection Routing Integration Tests

- Legacy direct-only graph, Collection-only graph와 mixed graph가 Shared/Gateway/Worker/
  Client validator에서 같은 pass/fail 결과를 사용한다. 각 list의 19/20은 성공하고
  21은 silent slicing 없이 실패한다. Malformed/non-canonical UUID, unknown item field,
  overlong/control display snapshot과 raw object echo를 거부한다.
- Route-safe Collection picker는 user-direct/active-Team/organization-manager effective
  `route` positive case와 read/manage/sync/domain-only, inactive membership, revoked,
  cross-organization, archived/deleted negative case를 검증한다. Response는 UUID와 optional
  approved safe label만 가지며 child/source/permission/hidden count를 포함하지 않는다.
  최근 unauthorized Collection 500개 뒤에 authorized Collection이 있는 fixture와 authorized
  Collection 501개 fixture로 permission scope가 limit보다 먼저 적용되고 authorized 결과가
  500개로 제한되는 순서를 검증한다.
- Draft/Agent Builder/optimizer/model-routing graph save는 direct KB effective `use`와
  source authorization, Collection `route`를 current editor로 다시 검증한다. 하나라도
  stale/forged/denied/cross-org이면 partial graph/success audit 없이 whole-write를
  rollback하고 safe generic error만 반환한다. Knowledge reference가 없는 root/nested legacy
  graph는 null organization/invalid legacy user identifier로 permission service를 만들지 않지만,
  malformed empty-list shape는 422 구조 오류로 유지한다. 과거 completed chunk가 남은
  `source_deleted` direct KB와 route grant/membership이 남은 `source_deleted` parent
  Collection은 picker, save, preflight와 runtime 모두 거부한다.
- Collection route는 있지만 child KB/source access가 전부 denied인 graph는 save할 수
  있고 runtime에서 zero candidate/no provider로 닫힌다. Save-time에 child membership이나
  source를 query하면 테스트 실패다.
- PostgreSQL coordinated revoke/save test는 revoke가 authorization read 전에 commit되면
  save가 실패함을 보이고, read 뒤 revoke 경합으로 configuration intent가 남더라도
  다음 MBA-232 invocation이 current permission으로 제외하며 save capability를 재사용하지
  않음을 검증한다.
- Deployment preview/create/activation/toggle과 nested workflow-node graph는 두 list를
  검증한다. Anonymous private Collection/direct KB와 source public exposure primitive가
  없는 source-managed content는 fixed blocker이고 hidden child ID/count를 반환하지 않는다.
  Direct/public-membership/Collection aggregate query 모두 `source_deleted` KB를 제외하는
  PostgreSQL predicate를 사용하고 direct KB에는 retrieval-visible completed chunk
  readiness를 적용하며 runtime resolver eligibility와 어긋나면 테스트 실패다.
  빠른 SQL compile test는 INNER JOIN 대상/조직 ON 조건/lifecycle/sync predicate를 검증하고,
  opt-in disposable PostgreSQL test는 active, source-deleted, archived, cross-organization,
  organization-mismatched membership이 실제 반환 집합과 candidate count에서 올바르게
  포함·제외되는지 migration 적용 스키마에서 검증한다. 같은 실제 DB 테스트에서 최근
  unauthorized 500개 뒤의 authorized Collection과 501개 authorized 결과 cap도 검증한다.
  Source-deleted parent Collection은 route permission이 남아 있어도 picker cap, save,
  preflight와 runtime candidate stream 어느 곳에도 포함되지 않아야 한다.
- 모든 production execution surface는 same resolver dependency를 주입한다. Direct-only,
  Collection-only와 mixed invocation은 resolver를 정확히 한 번 호출하며 ordered canonical
  candidate마다 retrieval을 최대 한 번 실행한다.
- Resolver policy zero-result와 insufficient evidence는 embedding/retrieval/provider를
  호출하지 않는다. Resolver infrastructure exception은 safe retryable workflow failure로
  Celery retry되고 `ragFailurePolicy=safe_no_result`로 낮아지지 않는다. Retry/redelivery는
  fresh snapshot을 열 수 있지만 exactly-once provider 호출을 주장하지 않는다.
- Agent Builder/optimizer/compare/copy/import/model routing/deployment snapshot은 unrelated
  edit에서 `knowledgeCollections`를 보존한다. Pre-execution sync는 direct KB만 처리하고
  Collection child를 열거하거나 connector를 호출하지 않는다.
- Public app/deployment graph는 두 reference list를 제거한다. API/SSE/error/log/trace/audit
  fixture는 Collection ID/name/provenance, hidden KB ID, raw graph/query/source/credential/
  provider payload가 없고 safe bucket/fixed code만 있음을 검증한다. Mixed successful
  retrieval에서는 explicit direct KB의 기존 KB/chunk/document lineage는 유지하지만
  Collection-derived evidence의 child KB/chunk/document ID와 per-KB rank는 result metadata, durable trace,
  audit 어디에도 나타나지 않는다. 최종 정렬·dedupe·top-k 이후의 `evidence_rank`는 direct와
  Collection-derived result/quality trace에 1부터 연속해서 나타나며 child KB별로 재시작하지
  않는다. Collection retrieval audit은 node target과 count bucket만
  포함한다. Collection-only/mixed trace는 authorized/selected KB exact count와 그 값에서
  유도되는 actual fan-out concurrency를 저장하지 않고 count bucket만 남긴다.
  Query-vector/fan-out INFO log도 KB/model/vector/failure exact count 대신 bucket만 기록한다.
  Anonymous/system actor와 invalid organization audit 경계도 같은 redaction을 쓴다.
- Worker-first canary는 구 task drain 뒤 Gateway write와 Client를 순서대로 노출하고,
  rollback은 Client/Gateway write 중지와 drain 뒤 Worker를 되돌린다. 구 Worker가
  Collection graph를 소비할 수 있는 상태에서는 rollout/rollback acceptance가 실패다.

## MBA-238 Internal Chatbot User Permission Integration Tests

- 실제 PostgreSQL production candidate adapter에서 같은 organization의 Dev Team 사용자,
  Planning Team 사용자, user-direct `operator` 사용자와 Knowledge 권한이 없는 사용자를
  한 fixture로 비교한다. 각 사용자는 자신의 team-bound 또는 user-direct KB `use`와
  선택한 Collection `route`를 모두 충족한 후보만 얻어야 한다.
- 다른 organization의 사용자와 동명 리소스는 target organization 후보에 포함되지 않는다.
  이 격리는 in-memory query fake가 아니라 실제 organization, membership, permission
  predicate로 검증한다.
- Runtime resolver가 반환하지 않은 KB는 embedding 또는 vector retrieval 입력에 전달되지
  않는다. 최종 후보가 0개이면 embedding, retrieval, LLM provider를 모두 건너뛰고 표준
  no-evidence 응답을 반환한다.
- Collection 유래 evidence와 권한 필터 관측값에는 child KB/Collection identity, raw content,
  정확한 denied count가 없어야 한다. 허용/거부 synthetic marker는 응답, citation,
  trace summary와 audit projection 전체에서 검사한다.
- 기존 MBA-232/MBA-233/MBA-241 테스트가 snapshot, bounded scan, resolver failure,
  citation redaction을 이미 검증하면 해당 테스트를 acceptance evidence로 재사용하고 같은
  단위 테스트를 MBA-238 이름으로 복제하지 않는다.

## Knowledge Base API Tests

### MBA-273 Initial Document Registration

- 빈 active manual KB에서 KB `write`가 있는 caller는 FILE/API/DB 중 하나의 최초 `Document(status=pending)`만 등록할 수 있고 detail의 `can_register_initial_document`는 등록 전 true, 등록 후 false다.
- Manual KB의 기존 Document는 `pending`, `processing`, `failed`, `completed` 상태와 무관하게 slot을 점유한다. Completed Document가 active version pointer를 가지고 있어도 두 번째 independent source는 `409 knowledge.document_slot_occupied`이며 response/log/audit에 기존 document id, filename, path, source config를 포함하지 않는다. Source-managed/non-manual KB는 Document 존재 여부를 공개하지 않고 `knowledge.document_registration_not_allowed`로 먼저 거부한다.
- KB `read`만 있거나 cross-organization/hidden/deleted KB인 caller는 기존 hidden/denied matrix를 유지한다. Source-managed 또는 `sync_state != manual` KB는 최초 manual registration을 fail-closed한다.
- 두 transaction이 같은 빈 KB에 동시에 등록하면 PostgreSQL KB row lock 뒤 하나만 commit되고 다른 하나는 conflict가 된다. Endpoint 권한 검사로 같은 Session identity map에 KB가 먼저 올라온 경우에도 canonical lock query는 `populate_existing`으로 DB 상태를 다시 읽어 concurrent lifecycle/source/version 변경을 반영한다. 최종 Document count는 1이며 rollback/commit failure가 partial row를 남기지 않는다.
- Backend-mediated FILE upload가 fast precheck 뒤 race에서 지면 해당 request가 생성한 storage artifact만 보상 삭제한다. DB flush 이전 실패는 cleanup-safe지만 commit 호출 이후 결과가 불명확한 실패에서는 이미 커밋된 Document reference 보호를 위해 자동 삭제하지 않는다. Cleanup failure는 provider exception/path/key를 노출하지 않는다. Presigned/direct object는 ownership이 확정되지 않으면 자동 삭제하지 않는다.
- Completed/active version을 가진 manual KB의 유일 Document를 삭제하면 shared document advisory lock이 먼저 획득되고, active pointer가 NULL로 전환되며 기존 active version은 `superseded`가 된다. 삭제 권한 검사에서 KB/Document를 같은 Session에 preload했더라도 advisory lock 뒤 canonical row lock query는 fresh DB 값을 다시 읽어 concurrent finalization을 놓치지 않으며, 그 사이 archived가 된 KB는 mutation 없이 hidden 처리한다. 삭제 commit 뒤 같은 KB에 replacement Document 하나를 등록할 수 있어야 한다. Legacy multi-document KB에서 삭제 대상이 아닌 sibling을 가리키는 active version은 임의로 해제하지 않는다.
- DB source modal이 이번 요청에서 Connection을 생성한 뒤 canonical document registration race를 잃으면 새 Connection을 보상 삭제한다. Connection delete는 owner row lock과 Document의 top-level `connection_id`, nested object `db_config.connection_id`, legacy serialized `db_config` reference check를 사용하며 이미 참조 중인 Connection은 `409 connection.in_use`로 보존한다. Serialized config가 malformed여도 대상 UUID를 포함하면 보수적으로 in-use 처리한다. Registration commit 결과가 불명확한 `knowledge.document_registration_unavailable`에서는 자동 삭제하지 않고, cleanup 실패의 raw response/config를 Client log나 toast에 노출하지 않는다.
- DB Document process/sync가 새 opaque `connection_id` reference를 저장하는 동안 owner Connection row lock을 commit까지 유지한다. Durable admission은 `Connection -> KnowledgeBase -> Document -> job` 순서로 fresh lock하고 Connection lifecycle UoW가 metadata/job flush와 commit을 함께 소유한다. 같은 Connection 삭제가 경합하면 저장이 먼저인 경우 delete는 committed reference를 보고 `409 connection.in_use`, 삭제가 먼저인 경우 저장은 `404 resource.hidden`으로 끝나며 dangling reference를 commit하지 않는다. 최초 조회 뒤 같은 Document가 먼저 갱신되면 fresh revision compare가 stale writer를 `409 connection.reference_conflict`로 전체 rollback한다. Connection lifecycle의 lock/flush/commit contention은 raw DB detail 없이 `503 connection.reference_busy`, 그 밖의 Connection lifecycle 저장 장애는 `503 connection.reference_unavailable`로 반환한다. Connection 참조 경계 밖의 알 수 없는 admission 저장 장애는 `503 ingestion.admission_unavailable`로 축약한다.
- `Document`가 없고 active `DocumentVersion`의 `legacy_document_id`와 `source_identity_id`도 모두 NULL인 legacy stale pointer KB는 registration의 fresh KB/version row lock 아래 pointer를 해제하고 version을 `superseded`로 전환한 뒤 replacement Document 하나를 생성한다. Version이 live document/source identity를 가지거나 source-managed/non-manual KB이면 자동 복구하지 않고 mutation 없이 fail-closed한다.
- Knowledge 최초 등록용 Presigned URL 요청은 대상 `knowledgeBaseId`, active organization과 KB `write`를 요구하고 occupied KB를 storage call 전에 거부한다. Workflow 입력 파일용 generic presigned 호출은 기존처럼 KB 식별자 없이 동작한다. Fast precheck 통과 뒤 발생한 race는 최종 upload의 canonical check에서 다시 거부된다.
- Endpoint와 ingestion orchestrator가 registration service를 우회해 `Document`를 직접 생성하는 production path가 없는지 architecture test로 고정한다.
- Client는 explicit `can_register_initial_document=true`에서만 최초 source action을 표시한다. Field 누락/false, occupied/pending/failed/source-managed 상태에서는 action을 숨기고 Collection 안내를 제공하며 stale 409 뒤 detail을 refresh한다.
- Demo seed의 fixed Knowledge Base별 Document count는 1 이하이고, 기존 인사 휴가·복지 fixture는 서로 다른 KB에 보존된다. Aggregate 검색이 필요한 seed graph는 Collection 또는 명시된 별도 KB reference를 사용하며 reset 반복 후에도 cardinality와 비대상 동적 KB 보존 계약을 유지한다. 검색 시연용 Collection의 모든 child KB는 `completed` Document와 1536차원 precomputed chunk를 하나 이상 가져야 하며, 단순 membership row 존재만으로 seed 성공으로 판단하지 않는다.

- KB create는 blank name을 DB insert 전에 거부하고 safe validation reason code만 반환한다.
- KB create는 255자를 초과하는 name을 DB insert 전에 거부하고 safe validation reason code만 반환한다.
- KB create는 empty, secret-like, token-like, allowlist 밖 `embedding_model`을 DB insert 전에 거부한다.
- KB create validation failure는 partial KB row를 만들지 않고 raw request value, stack trace, SQL, credential을 response, audit, log에 노출하지 않는다.
- KB create는 trimming 후 저장되는 name과 `embedding_model`이 기존 API response shape를 깨뜨리지 않는다.
- 같은 organization 안에서 동일한 KB `name` create는 이름만으로 conflict 처리하지 않는다. KB name은 display label이며 identity가 아니므로 `knowledge_base_id`, source identity, sync/lifecycle state, safe metadata로 구분한다.
- 동일 문서의 version은 여러 개를 동시에 retrieval-visible 후보로 만들지 않는다. 내부 문서는 active/head pointer가 가리키는 ready version만 검색 노출하고, 외부 source-managed 문서는 정상 sync/finalization 이후 최신 active ready version만 검색 노출한다. Sync 실패나 stale 상태에서는 기존 active ready version만 warning과 함께 유지할 수 있으며, 이전/superseded/pre-finalized version은 selectable-ready 또는 evidence 후보가 아니다.
- Source-managed KB의 동일 source item 중복은 KB `name`이 아니라 protected source identity/source sync lineage invariant로 검증한다.
- KB detail과 direct document detail은 같은 document metadata projector를 사용한다. Safe progress/state/timestamp/processing option과 finite non-negative cost estimate만 반환하고, `api_config` 및 encrypted config field, `connection_id`, source/connector identifier, DB connection metadata, unknown nested field는 KB `read` 또는 더 강한 resource state에서도 반환하지 않는다.
- Document metadata projector는 non-mapping input, 잘못된 type, out-of-range progress, invalid timestamp, non-finite/negative cost와 oversized/unknown string을 생략하며 projection 실패 때문에 response 전체가 500이 되거나 내부 값을 그대로 fallback하지 않는다.
- KB `write` actor의 document settings 화면은 전용 edit-config API에서 기존 segment/chunk/selection과 DB selection/opaque connection reference를 복원한다. Read-only actor는 endpoint에서 hidden/denied되고, encrypted API URL/header/body와 credential/connection detail은 write actor에게도 반환되지 않는다. Edit config가 malformed, 개별/aggregate/serialized-size budget 초과 또는 load failure면 Client는 DB/API preview/process를 호출하지 않고 기존 저장 설정을 보존한다. 응답은 `Cache-Control: no-store`다.
- Document settings route 전환은 permission/readiness와 모든 source-specific state를 즉시 reset한다. Cleanup/generation 이후 늦게 도착한 이전 KB/document response는 현재 설정이나 action gate를 변경하지 못하며, 새 fetch failure도 이전 문서의 설정으로 action을 다시 열지 않는다.
- KB detail/direct document의 `error_message`와 progress SSE의 `message`/`error`는 arbitrary legacy exception 또는 processing-step 원문을 포함하지 않는다. Fixed safe message만 반환하고 raw input marker가 JSON/SSE/toast/log capture에 나타나지 않아야 하며 progress는 0..100 범위 밖 값을 전달하지 않는다. Redis progress는 `indexing`/`processing`에서만 사용하고 pending/approval 상태는 stale 100을 무시한다. SSE는 no-cache/no-store와 buffering disable header를 반환한다.
- Document settings의 PDF 원본 preview는 `X-Organization-Id`가 명시된 API client 요청으로 content를 가져오고, ephemeral Blob URL을 browser-native `object[type="application/pdf"]`와 `noopener noreferrer` 새 탭 fallback에 사용해야 한다. Native `object`/anchor가 organization header 없는 content API URL을 직접 요청하면 테스트 실패다. Local/external PDF content response는 `X-Content-Type-Options: nosniff`를 반환한다. PDF viewer를 위해 iframe sandbox에 `allow-scripts`를 추가하면 테스트 실패다.
- PDF가 아닌 FILE 원본 preview는 같은 authorized Blob을 `allow-same-origin`만 허용한 scriptless sandbox iframe에서 렌더링한다. Preview iframe에 `allow-downloads` 또는 `allow-scripts`가 있으면 테스트 실패다. Missing KB/document id 또는 filename에서는 content를 요청하지 않고, content fetch failure는 raw 오류를 표시하지 않는다. 문서 scope 전환/unmount는 요청을 abort하고 생성된 Blob URL을 revoke하며, 늦게 도착한 이전 scope content를 렌더링하지 않는다. 현재 document filename에 따라 PDF와 non-PDF viewer를 다시 선택한다.
- 처음부터 `completed`인 document settings route는 preview를 유지하고 3초 후 KB 상세로 이동하지 않는다. 같은 화면에서 `indexing` 또는 `processing` 상태를 실제로 관찰한 뒤 `completed`가 된 경우에만 기존 완료 후 이동 동작을 수행한다.

## Connector And Egress Tests

- Connector preview/test/fetch는 승인된 outbound guard factory 밖의 raw socket, ad hoc HTTP client, custom dialer를 사용할 수 없다.
- Knowledge API source, `/api/v1/rag/proxy/preview`, remote FILE ingestion과 external preview/content fetch는 각각 등록된 operation profile을 사용한다. 승인 origin의 path/query와 bounded same-origin redirect query는 유지하고 userinfo, fragment, Host authority 위조, HTTP/non-443, unknown operation, private·metadata DNS result, DNS-to-dial 변경, peer mismatch, HTTPS downgrade, cross-origin redirect와 ambient proxy는 credential/body 전송 전에 거부된다. Cross-origin redirect는 대상 DNS 조회 전 차단된다.
- Guarded response는 허용 content type과 byte 상한을 stream 중 적용한다. Denial/timeout/oversize 실패에는 source URL/query/header/body, resolved/peer IP와 raw exception이 없고 기존 active ready version은 유지된다.
- `/api/v1/rag/proxy/preview`, URL upload/preview(`s3FileUrl`, `apiUrl`), crawler, sitemap, future web/API connector, DB/SSH/SaaS/object-storage probe는 모두 central guard를 통과한다.
- `/api/v1/rag/upload` 신규 KB 생성은 `X-Organization-Id` active organization을 사용하며 primary organization fallback을 사용하지 않는다. 기존 KB 업로드는 KB organization과 active organization이 다르면 hidden/not-found로 닫는다.
- DNS rebinding, private IP redirect, link-local/metadata IP, private network target, unsupported scheme, HTTPS downgrade, `verify=false`, oversized response, timeout을 거부한다.
- IPv4 obfuscation, IDNA/punycode/CNAME trick, open redirect chain, redirect 시 sensitive header forwarding, compression/zip bomb payload, unapproved proxy/CA configuration, rate-limit bypass를 거부하거나 safe cap으로 제한한다.
- File/page artifact content는 egress guard 이후에도 untrusted로 처리한다. 지원 file type/content type allowlist 밖이면 document version, chunk, embedding, prompt-visible artifact가 생성되지 않는다.
- Macro-enabled Office document, embedded object/script, executable payload, active HTML/script, external reference를 포함한 artifact는 별도 opt-in policy 없이는 fail-closed 또는 remediation 상태가 된다.
- Archive ingestion은 nested depth, expanded size, contained file count, nested archive count cap을 적용하고, cap 초과 또는 archive 내부 executable/script/macro-enabled file을 indexing-visible artifact로 만들지 않는다.
- Malware/content scan result가 `unknown`, timeout, error이면 high-risk binary/Office/archive는 ready/indexing-visible 상태로 진행하지 않는다.
- Parser/extractor는 sandbox 또는 least-privilege worker에서 실행되고, parser가 document 내부 script/macro/external URL을 실행하거나 따라가면 테스트 실패다.
- Content safety failure, unsupported type, parser exception은 raw file bytes, active content marker, parser raw error를 audit, trace, log, retry/dead-letter payload, user-facing response에 남기지 않고 safe reason code와 remediation state만 남긴다.
- DB adapter는 arbitrary SQL을 거부하고 승인된 read-only probe/schema introspection만 cap 안에서 허용한다.
- Proxy mode DB ingestion은 deployment-managed Connector allowlist의 custom PostgreSQL port를 runtime guard까지 동일하게 적용하고, direct local/development 기본값은 `5432`를 유지한다.
- Workflow Worker의 `gevent` 환경에서 DB ingestion local relay는 blocking driver와 독립된 OS-native accept/forward loop를 사용해야 한다. Relay 교착을 일반 `sync_failed` stale-index fallback으로 오인하거나 proxy 실패 뒤 direct dial하면 테스트 실패다.
- SSH adapter는 arbitrary command execution과 승인되지 않은 tunnel/proxy behavior를 거부한다.
- Object storage adapter는 policy가 bounded listing을 명시적으로 허용하지 않는 한 과도한 bucket/listing operation을 거부한다.
- Egress/adapter error는 sanitized reason code를 반환하고 credential이나 raw connection string을 포함하지 않는다.
- Slack connector baseline은 channel을 collection으로, thread/huddle recap/canvas/bot-generated meeting summary/pinned-message group을 document-level KB로 매핑한다.
- Slack/meeting artifact-level ACL이 있으면 artifact ACL과 containing channel/workspace ACL의 교집합을 통과한 requester만 retrieval 후보를 얻는다.
- Slack/meeting artifact ACL을 확인할 수 없으면 fail-closed 또는 remediation 상태가 되고 channel membership만으로 공개되지 않는다.
- Slack/meeting DM, raw audio, raw transcript ingestion은 별도 opt-in policy 없이 실행되지 않는다.
- MCP/API source connector는 allowlist 밖 operation을 호출하지 않는다. 임의 MCP tool selection 또는 LLM-directed source raw data fetch가 가능하면 테스트 실패다.
- MCP/API source connector response size cap 초과, timeout, raw tool exception은 raw body/error 없이 safe reason code로 닫힌다.
- Runtime authorization primitive(`check_access_batch` 또는 bounded single `check_access`)가 없는 source는 private source-managed KB retrieval을 fail-closed 처리한다.
- Raw payload normalization 전 connector failure에서도 connector log, retry/dead-letter payload, audit, trace에 변환 전 raw payload가 남지 않는다.

## Sync And Ingestion Tests

- Sync lease는 두 worker가 같은 source item을 동시에 finalize하지 못하게 한다.
- 같은 document-level KB에 대한 concurrent ingestion은 하나의 finalization만 성공한다.
- A document moved to queued `indexing`/`processing` state must become `failed`
  with safe progress metadata only when no active fencing token and no
  processing progress/chunk/version artifact appears before the processing-start
  timeout.
- A document with an active fencing token must also become `failed` after the
  active processing stall timeout when no processing progress/chunk/version
  artifact exists, so crashed workers cannot leave the UI in processing forever.
- Redis lock/progress storage unavailable must not leave a document in infinite
  `indexing`/`processing`. Processing either continues through the local fallback
  lock path or closes with a safe failed state.
- Active processing with a recent DB progress heartbeat must remain in progress,
  but an old active fencing token with only a pre-finalized
  `DocumentVersion(status=indexing)` and no chunk/ready version must become
  `failed` after the active stall timeout.
- A document incorrectly marked failed with the processing-start timeout message
  must recover to completed when retrieval-visible chunks or a ready document
  version already exists.
- `content_hash`, chunking fingerprint, embedding model은 chunk/index artifact finalization 성공 전에는 새 processed state로 commit되지 않는다.
- Finalization 실패 뒤 다음 retry는 stale `content_hash` 때문에 skip하지 않고 다시 처리한다.
- Content cursor는 active version finalization 이후에만 전진하고, ACL/permission watermark는 source ACL state와 candidate cache invalidation commit 이후에만 전진한다.
- Content cursor 전진 실패와 ACL watermark 전진 실패는 서로를 암묵적으로 commit하지 않는다.
- Source content가 바뀌지 않고 ACL만 바뀐 경우에도 Source Authorization Provenance와 permission freshness를 갱신한다.
- Source deletion은 KB sync state를 `source_deleted`로 만들고 retrieval에서 제외하지만 audit/citation history를 기본 삭제하지 않는다.
- Connector access loss는 sync/permission state를 stale 또는 unverified로 표시하고 `source_deleted`로 처리하지 않는다.
- Indexing failure는 기존 active version을 retrieval 가능 상태로 유지한다.
- Successful indexing은 하나의 finalization contract 안에서 active version을 교체하고 이전 version을 superseded로 표시한다.
- Finalization 중 worker crash는 outbox/recovery scanner로 복구하고 pre-finalized artifact를 노출하지 않는다.
- Active pointer swap, previous version `superseded` 표시, processed state commit, cleanup/finalization outbox insert는 같은 DB transaction 안에서 일어난다.
- Pointer swap 이후 outbox insert 전에 crash가 발생하는 window를 허용하지 않는다.
- External index success 이후 DB finalize failure가 발생하면 이전 active version을 유지하고 orphan cleanup을 queue에 넣는다.
- DB finalize success 이후 object storage/vector index cleanup failure가 발생하면 새 active version은 유지하고 cleanup을 retry한다.
- DB source sync 또는 shared vector save path가 같은 KB/document chunks를 동시에 교체하려 할 때 advisory lock 또는 versioned chunk set이 lost update를 막는다.
- Target cleanup outbox/reconciler cutover는 Document/KB delete가 DB commit 전에 object storage 또는 raw artifact를 먼저 삭제하지 않고, physical cleanup을 outbox/reconciler가 idempotent하게 수행함을 검증한다.
- Current hard-delete baseline은 KB delete가 Knowledge lifecycle service boundary를 통과하고, organization field가 잘못된 legacy row를 포함해 해당 KB를 참조하는 direct KB permission row cleanup이 hard delete와 같은 transaction에서 먼저 일어나며, storage adapter 생성 또는 object delete 실패가 API 실패나 raw path/raw exception log 노출로 이어지지 않음을 검증한다. Storage adapter는 provider 세부정보가 없는 typed delete error를 호출자에게 전달하고, lifecycle service는 한 object cleanup 실패 뒤에도 나머지 object cleanup을 계속한다. Permission cleanup, ORM delete 또는 DB commit이 실패하면 session rollback 후 예외를 전파한다. 이 baseline은 MBA-184의 durable audit/outbox와 target cleanup outbox/reconciler cutover를 대체하지 않는다.
- S3 delete reference는 configured bucket의 `s3://`, virtual-host, 승인된 path-style URL과 canonical `uploads/` key만 허용한다. URL-encoded 공백/한글 key는 한 번 decode하고, bucket/host mismatch, HTTP, query/fragment, 빈 key, control/dot/backslash segment, `uploads/` 밖 key는 provider 호출 전에 safe typed error로 거부한다.
- Local delete reference는 configured upload root 내부 resolved path만 허용한다. Root 밖 절대/상대 경로와 symlink escape는 파일을 삭제하지 않고 safe typed error로 닫는다.
- Local storage는 컨테이너 기본 경로(`/app/uploads`)를 생성한 뒤 임시 파일 write/flush/delete probe까지 통과한 경우에만 사용한다. 기본 경로가 permission/read-only 오류로 생성되지 않거나 기존 directory가 실제로 쓰기 불가능하면 프로젝트 `uploads/` 경로를 같은 방식으로 검증해 fallback하며, 선택한 default root는 프로세스 안에서 lock으로 한 번만 고정한다. Probe artifact는 남지 않아야 하고 동시 service 생성도 다른 root를 선택하면 안 된다. 명시한 upload root는 같은 write probe 실패를 전파하고 fallback하지 않는다.
- Backend upload와 presigned upload가 생성한 S3 key 및 Local path는 같은 canonical builder/delete validator round-trip을 통과해야 한다. Filename 또는 user segment에 slash/backslash, `.`/`..`, control character, 과도한 길이가 있으면 object 생성/presign 전에 safe typed error로 거부하며, delete validator를 완화해 legacy unsafe key를 허용하지 않는다.

## Client/UI Tests

- Source upload 성공 후 create modal은 KB 상세 source list로 돌아가며, 등록된 `pending` source가 목록의 처리 CTA를 통해 document settings 화면으로 이동할 수 있어야 한다.
- KB 상세 source 목록은 `pending` document에 `처리 시작` action과 "처리 시작 전에는 RAG 검색에 사용되지 않는다"는 안내를 표시한다.
- KB 상세 source 목록은 `failed` document에 `재처리` action을 표시하고, `completed` document에는 처리 시작 CTA를 표시하지 않는다.
- Pending/failed processing CTA는 document settings 화면으로 이동하며 raw file path, source title, hidden KB id를 새로 노출하지 않는다.
- 지식 테스트 모달의 모델 목록, 일반 검색과 AI 답변 요청은 현재 활성 조직의 `X-Organization-Id` 헤더를 전송한다.
- Organization 또는 KB가 바뀌거나 modal scope가 닫히는 동안 이전 검색 요청이 완료되어도 해당 결과는 새 scope에 표시되지 않는다. 오류 UI는 raw backend/provider/source detail을 표시하지 않는다.
- AI 답변은 현재 organization에서 조회된 유효한 chat model이 선택된 경우에만 요청하며, 모델이 없거나 목록 응답 shape가 잘못되면 generation model이 빈 요청을 전송하지 않는다.
- 초기부터 `completed`인 document settings 화면은 자동 이동하지 않는다. 같은 document scope에서 `indexing|processing`을 관찰한 뒤 `completed`에 도달한 경우에만 KB 상세 이동을 한 번 예약한다. 이 active 상태는 현재 화면의 성공한 process/approval 요청 또는 처음부터 처리 중인 문서를 관찰하면서 시작될 수 있다.
- 비용 승인 취소, process/approval 요청 실패, `failed` 완료는 이동 intent를 만들지 않는다. Organization 전환 직후 이전 render의 handler는 process/analyze/preview/approval 요청을 시작하지 않으며, 이전 active organization/KB/document의 늦은 initial fetch, process/approval, SSE 또는 polling 결과는 현재 화면의 status/progress/edit gate를 변경하거나 이동을 예약하지 않는다.

## Retrieval And Agent Tests

- Gateway public route는 KB `use` 확인 뒤 RetrievalService를 호출하고, Workflow runtime은 MBA-232 resolver가 반환한 authorized candidate만 model projection에 전달한다. Denied/noncandidate KB의 model identifier는 batch predicate, 결과, error, audit, trace에 나타나지 않아야 한다.
- 같은 model과 여러 model을 사용하는 authorized KB를 조회해도 implicit path의 `LLMModel` query는 invocation당 최대 1회다. Explicit verified model과 authorized candidate 0건 경로는 0회다.
- Missing, inactive, non-embedding, ambiguous model identifier와 runtime KB model이 binding과 불일치하는 후보는 LLM client, embedding provider, vector store 호출 전에 제외된다.
- Workflow fanout은 query vector와 immutable scalar binding을 재사용해 per-KB model ORM 조회를 수행하지 않는다. Projection 저장소 장애는 per-KB fallback이나 provider 호출 없이 safe no-result 또는 정책에 따른 sanitized node failure로 닫힌다.
- Capability-required Workflow fanout은 authorized 후보가 0개면 query-embedding policy/capability/provider/usage를 호출하지 않는다. 후보가 있으면 distinct canonical embedding model마다 `query_embedding` capability와 provider attempt를 한 번만 사용하고 같은 model 후보만 vector를 공유한다.
- Query-embedding policy, credential `use`, verified relation, ADR-0067 egress 또는 ADR-0069 durable operation 경계가 없거나 stale하면 generation credential·execution user·owner/default fallback 없이 embedding과 main provider 호출 전에 fail-closed한다.
- Query embedding success/deny/provider failure의 API, audit, trace, usage와 task payload에는 raw query/vector/credential/config/capability scope/provider payload와 exact hidden candidate count가 없다.
- `internal_chatbot` 실행의 current user는 Runtime permission helper에 그대로 전달되고, 해당 user의 KB permission 또는 source ACL이 거부한 후보는 retrieval 전에 제외된다.
- 공개 `chatbot`은 execution subject나 owner fallback 없이 anonymous public-only로 검색하며, `internal_chatbot`의 public surface 실행은 safe 404로 거부된다.
- Auto mode는 collection route helper와 KB permission/source ACL helper 결과로 candidate set을 만든다.
- Auto mode의 collection/KB cap은 authorization 전 임의 row cap이 아니라 route/use/source ACL helper를 통과한 authorized subset에 적용한다.
- Builder/recommendation Auto mode에서 명시 `collection_ids`가 없으면 organization 전체 collection이 아니라 actor가 route할 수 있는 collection subset에서 시작한다. MBA-232 Workflow runtime은 missing/empty Collection scope를 0개로 유지하며 이 fallback을 사용하지 않는다.
- Router는 authorized safe candidate와 safe metadata만 받는다.
- Router는 raw source ACL fact, hidden KB id, raw source title/path/url, exact hidden count, raw content를 받지 않는다.
- Knowledge RAG Recommendation Adapter는 `KnowledgeCandidateResolver`가 반환한 safe KB candidate만 ranking하고, permission/source ACL row를 직접 조회하거나 해석하지 않는다.
- Recommendation response item은 초기 구현에서 `candidate_type=knowledge_base`만 사용한다. Collection label과 linked KB count는 `source_collection_summary` safe metadata로만 제공한다.
- `materialized_knowledge_bases`는 LLM node `knowledgeBases`로 변환 가능한 safe KB ref만 포함하고, `MAX_RAG_RETRIEVAL_KBS=20` cap을 넘지 않는다.
- Safe label이 없는 KB recommendation은 raw KB name을 fallback으로 사용하지 않고 `null` 또는 generic label만 사용한다.
- Recommendation request는 raw `workflow_intent`나 raw natural language 전체가 아니라 `StructuredRequest` 기반 `intent_summary`, `node_purpose_summary`, `knowledge_requirement`, `pending_resolution_ref`, `safe_workflow_context_summary`를 사용해야 한다. 이 safe structured input도 durable raw storage 금지, 길이 cap, control character normalization을 통과해야 한다.
- Workflow Builder RAG recommendation은 Agent Builder client가 직접 호출하는 public client endpoint가 아니라, Agent Builder backend가 server-resolved context를 확정한 뒤 호출하는 Knowledge domain boundary로 취급해야 한다.
- Public HTTP recommendation endpoint는 `mode=explicit_kb`와 raw KB id 기반 scope opening을 safe validation error로 거부한다. `mode=auto`에 raw KB/collection id가 섞여 있으면 권한 판단에 사용하지 않고 무시한다.
- Trusted backend/internal service boundary는 safe handle, authorized picker, server-resolved context를 통과한 경우에만 explicit KB 후보를 recommendation scope로 사용할 수 있다.
- Recommendation response는 `status`, `resolution_id`, `requirement_id`, `recommendations`, `clarification_options`, `user_safe_warning`, `fallback_reason` top-level envelope를 사용해야 하며 recommendation item list만 단독으로 반환하지 않아야 한다.
- Recommendation ranking은 `KnowledgeCandidateResolver`가 만든 server-issued reference 또는 같은 backend 내부 service call의 safe candidate set만 사용해야 하며, raw KB id나 raw source metadata로 권한 후보를 직접 만들지 않아야 한다. HTTP 또는 serialized boundary에서는 full candidate set 객체가 아니라 reference만 사용해야 한다.
- Recommendation ranking의 `score`는 DB 저장값이 아니라 요청 시점 계산값이어야 한다. Agent Builder가 구조화한 `safe_query_topics`가 `kb_relevance` 1차 입력이어야 하며, `웹훅`, `워크플로우`, `챗봇`, `KB` 같은 action/UI terms는 relevance를 올리지 않아야 한다. Relevance matching은 `safe_label`, `kb_safe_description`, `kb_safe_topics`만 사용하고 `collection_safe_label`, `collection_safe_topics`, Collection name/description, collection id/count를 사용하지 않아야 한다.
- 일반 LLM node 생성처럼 KB safe metadata와 관련 없는 요청은 계층형 사용자 선택 응답에서 0점 후보를 표시할 수 있어도 Intent LLM의 `knowledge_candidates`에는 이를 포함하지 않아야 한다. 양수 relevance 후보는 안정 정렬 후 최대 20개까지 유지되어야 한다.
- Manual KB는 `name`/`description`을 sanitizer, length cap, secret/url/path 제거를 통과한 뒤 safe label/topics comparison text로 자동 생성할 수 있어야 한다. Source-managed KB는 display-policy-approved source safe metadata가 없으면 raw source-derived name/title/path/url을 safe label/topics 또는 keyword score 입력으로 사용하지 않아야 한다.
- KB detail UI는 `safe_label`과 `kb_safe_topics` 자동 생성 버튼을 각각 제공해야 한다. 저장 시 전용 `PATCH /api/v1/knowledge/{kb_id}/safe-metadata`는 KB `manage`를 확인하고 allowlisted 필드만 저장하며 raw source URL/path/title, secret-like value를 제거해야 한다. Creator 여부와 무관하게 effective resource `manager`는 `write`와 `manage`를 모두 포함하므로 일반 설정과 safe metadata를 편집할 수 있고, `builder`는 일반 설정만 편집할 수 있어야 한다. KB가 visible한 operator/viewer의 safe metadata mutation은 `403 permission.denied`, 완전히 invisible하거나 cross-organization인 대상은 `404 resource.hidden`이어야 한다. Detail capability에 따라 일반 설정과 safe metadata UI가 분리되어야 하며, audit의 `safe_metadata` before/after 값은 마스킹되어야 한다. 저장된 manual KB safe metadata는 Agent Builder recommendation에서 자동 생성값보다 우선해야 한다.
- Recommendation tokenizer는 한국어/영어/숫자 혼합 builder intent에서 `사내문서1`, `KB`, `웹훅`, `사내`, `문서`, `챗봇` 같은 safe term을 분리할 수 있어야 한다.
- Recommendation response item은 `score`, `confidence`, `reason_category`, `threshold_result`를 포함해야 하며 raw retrieval/provider score나 hidden resource identity를 노출하지 않아야 한다.
- Recommendation threshold 값은 구현 설정값으로 관리되고, 테스트 fixture에서는 고정되어 `high_confidence`, `close_score`, `below_threshold` 분기가 재현 가능해야 한다.
- Recommendation candidate set은 `source_deleted` KB와 current-valid active ready document version이 없는 KB를 selectable ready 후보에서 제외해야 한다. ADR-0070 enforcement 뒤 legacy chunk는 privacy-compliant active pointer가 한 번도 확정되지 않았고 eligibility가 retire되지 않은 document만 frozen wave membership, frozen/current platform·Organization validity epoch equality, `privacy_legacy_grace_v1` hard max와 DB-time half-open cutoff를 prefilter/final evidence gate 모두 통과할 때 허용한다. Invalidating epoch commit은 item projection/cleanup 전 즉시 legacy를 제외하고 compliant pointer 뒤 stale/invalid manifest는 legacy로 fallback하지 않는다.
- 권한 확인된 KB에 document row나 pre-finalized chunk artifact가 있지만 active ready version 또는 legacy retrieval-visible chunk가 없으면, Recommendation/Builder picker는 이를 권한 없음이나 숨겨진 KB처럼 조용히 숨기지 않고 `candidate_not_ready` 또는 `indexing_in_progress` 수준의 safe warning/disabled option으로 표시해야 한다.
- KB detail response의 `documents[].chunk_count`와 LLM node Knowledge Base picker의 selectable-ready 판단은 같은 retrieval-visible 기준을 사용해야 한다. Current-valid active ready document version이 있으면 해당 version chunk만 센다. Current MBA-105 compatibility에서 active version pointer가 없는 legacy KB는 legacy unversioned chunk만 fallback으로 세지만, ADR-0070 enforcement 뒤 compliant pointer history가 있거나 eligibility가 retired이면 active manifest stale/invalid와 남은 physical legacy/cutoff에 관계없이 legacy count는 0이다. Pointer history가 없는 경우에도 allowlist/wave 부재, frozen/current validity epoch 불일치, unsupported grace contract, cutoff equality/경과와 stale cache/vector result는 count/ready 근거가 아니며 frozen membership, epoch equality, hard max와 DB-time cutoff를 모두 통과해야 한다.
- Completed가 아닌 document, `ready`가 아닌 active/pending version, superseded/failed/pre-finalized version chunk, active version과 연결되지 않은 stale chunk는 `documents[].chunk_count`와 selectable-ready 판단에 포함하지 않는다.
- Builder/recommendation Auto mode에서 route-allowed collection link 후보가 없고 client가 collection scope를 명시하지 않은 경우, Gateway resolver는 직접 권한 확인된 retrieval-visible KB를 fallback 후보로 반환할 수 있다. 명시적으로 빈 collection scope를 보낸 경우에는 direct fallback을 적용하지 않고 후보 없음으로 유지해야 한다. 이 동작을 MBA-232 Workflow runtime resolver에 재사용하면 테스트 실패다.
- 기존 active ready version은 유지되지만 sync state가 `stale` 또는 `failed`인 KB는 후보로 남을 수 있으며, safe warning과 score penalty 또는 낮은 confidence가 함께 반환되어야 한다.
- Adapter unavailable이고 권한 확인된 safe 후보 선택지가 있으면 `status=clarification_required`, `fallback_reason=adapter_unavailable`, `clarification_options`를 반환해야 한다. Safe 후보 선택지도 없으면 `status=unavailable`과 safe fallback reason으로 닫아야 한다.
- Recommendation provenance는 `recommendation_strategy`, `safe_reason_code`, `used_signals`, safe matched terms, bucketed counts 같은 allowlist만 포함하고 raw source title/path/url, hidden id/name, exact denied count를 포함하지 않는다.
- `high_risk_domain` hint는 `strict_citation` 같은 RAG option 추천에만 영향을 주고 권한, source ACL, policy block 결정을 대체하지 않는다.
- No recommendation result는 사용자 확인 필요 상태를 기본값으로 만들며, Builder 정책 gate 없이 자동으로 RAG 없는 LLM node를 생성하지 않는다.
- MBA-145 Agent Builder MVP는 Knowledge Skill body/checklist를 prompt context로 직접 로드하지 않는다. 후속 Skill 사용 흐름에서도 Workflow Builder는 safe skill metadata만 받고 raw skill body, hidden source reference, restricted document list를 받지 않는다.
- Skill Context Loader는 후속 target 흐름에서만 선택된 skill의 redaction-safe checklist/body를 빌더 단계에 필요한 시점에 로드하고, 실제 문서 내용은 workflow 테스트 또는 실행 시점 authorized retrieval로 가져온다.
- Skill source-of-truth tier는 LLM node의 RAG 옵션 구성과 routing/procedure hint로만 사용되고, citation/evidence는 KB/document version/chunk/decision record를 가리킨다.
- Workflow 실행 시점 RAG는 명시적으로 resolve된 execution subject가 있으면 해당 subject 기준으로 KB permission/source ACL을 평가한다. Subject가 없으면 workflow owner fallback 없이 anonymous public-only로 낮추고, 모호한 subject는 private retrieval fail-closed로 처리한다.
- `subject_type="organization"` source-policy KB use grant는 active organization member에게만 적용되고 removed/suspended/invited/non-member user에게는 적용되지 않는다.
- Runtime source authorization은 `check_access_batch`를 우선 사용하고, batch 미지원 source의 single `check_access` fallback은 bounded concurrency, per-call timeout, aggregate timeout을 강제한다.
- `check_access_batch`가 일부 `denied`, `unknown`, timeout을 반환하면 해당 evidence만 fail-closed 제외되고 raw source error나 denied item title/path는 응답/trace/log에 남지 않는다.
- 위 두 live runtime source authorization 항목은 후속 target flow다. MBA-232 candidate resolver test는 materialized provenance만 소비하고 live batch/single/cache 호출이 전혀 없음을 별도로 고정한다.
- 운영 `general RAG`도 KB permission/source ACL/final evidence gate를 통과한다. Test fixture에서 권한 없는 문서는 `general`, `permission_scoped`, `task_aware` 모든 mode의 prompt/citation/trace에 들어가지 않는다.
- `general RAG`는 authorized resource 안의 broad retrieval로 동작하고, `task_aware` 또는 `permission_scoped` mode는 같은 authorized resource 안에서 더 작은 evidence set을 선택한다.
- Query rewrite가 켜져도 user query와 safe skill/template만 입력으로 사용하며, 권한 없는 KB/문서를 candidate로 만들지 못한다.
- Query rewrite 결과 원문은 durable audit/trace/usage metadata, cache key, log에 저장되지 않고 `query_rewrite_applied`, `query_rewrite_strategy` 같은 safe summary만 남는다.
- Source-of-Truth Tier는 authorized evidence 안에서 ranking/tie-break에만 영향을 주며 KB permission/source ACL/final evidence gate를 대체하지 않는다.
- 여러 collection에 같은 KB가 포함되면 `knowledge_base_id` 기준으로 dedupe하고 safe attribution rule을 유지한다.
- Retrieval은 active ready document version만 검색한다.
- Chunk/document metadata가 dict/object가 아닌 문자열, list, corrupted JSON-like value로 저장되어 있어도 retrieval metadata summary 생성은 crash하지 않고 빈 safe metadata로 낮춰 처리한다.
- Permission/source ACL/final evidence failure는 fail-closed evidence exclusion이며 partial operational success로 처리하지 않는다.
- 일부 authorized KB의 operational failure는 `partial_result=true`, bucketed reason summary, failed-candidate bucket, retryability를 포함한 safe partial result를 반환할 수 있다.
- 모든 KB retrieval failure는 승인된 API matrix에 따라 safe no-result 또는 terminal operational error 중 하나로 반환한다.
- Authorized source에서 evidence가 없는 경우는 성공한 empty evidence response이며 hidden resource를 암시하지 않는다.
- Workflow LLM node의 candidate 0개 또는 `no_evidence` 사용자 응답은 `요청하신 문서를 찾을 수 없거나 접근 권한이 없습니다.`로 일반화하며, 문서 존재 여부와 실제 권한 실패를 구분해서 노출하지 않는다.
- Evidence sufficiency policy가 `minimum_evidence` 또는 `strict_citation`일 때 evidence가 없거나 score/citation coverage가 부족하면 `evidence_sufficient=false`와 safe `insufficiency_reason`을 반환하고 추측 답변을 생성하지 않는다.
- `insufficiency_reason`은 권한 없는 문서명, hidden KB id, exact denied count를 포함하지 않는다.
- Retrieved context, memory summary, upstream node output, external connector content에 `ignore previous instructions`, `system prompt`, 역할 위장 같은 prompt injection성 지시문이 포함되어도 LLM system/developer policy와 사용자 명시 요청보다 우선하지 않는다.
- Standalone Agent answer와 Workflow LLM node RAG path는 retrieved context를 system prompt 본문에 직접 합치지 않고 untrusted evidence delimiter로 감싸며, 의심 지시문 라인을 redaction하거나 무해화한다.
- Workflow LLM node의 system/assistant prompt template에 upstream referenced variable이 포함되면 원문 value는 privileged role에 직접 렌더링되지 않고 untrusted evidence block으로 분리된다.
- Prompt injection guard 테스트 fixture는 악성 chunk 원문이 provider messages의 system role, audit metadata, trace metadata, answer summary에 저장되지 않는지 확인한다.
- Explicit KB id not found, outside org, archived/deleted, requester source authorization denied, source ACL stale/unmapped/ambiguous/unverified/revoked, permission-unverified는 matrix가 요구하는 동일한 safe resource-hidden shape를 따른다.
- Hidden/resource-hidden path의 external JSON/SSE `reason_code`는 `resource.hidden`으로 일반화되며 `source_authorization.denied` 또는 `source_acl.stale/unmapped/ambiguous/unverified/revoked` 세부 reason을 반환하지 않는다.
- PII/final evidence policy block은 answer delta나 citation content preview가 emit되기 전에 발생한다.
- Workflow LLM node RAG path도 `classification=pii` chunk를 외부 LLM context에 넣기 전에 차단하고, safe no-result 또는 fail-node 정책에 따라 닫는다.
- Live-linked mode에서 requester-scoped source-side search API가 없는 source는 일반 RAG 후보가 아니다.
- Live-linked broad service-account source-side search가 title, snippet, count, score를 authorization 전에 반환하면 기본 구현은 실패해야 한다. Opaque source ref만 후보로 전달하고 runtime source authorization 이후에 metadata를 노출하는 flow만 허용한다.
- Live-linked source-side search가 unauthorized item을 반환해도 prompt, citation, trace, response에는 해당 item의 title/snippet/count/score가 나타나지 않는다.
- 현재 standalone single-KB Agent answer lifecycle과 same-scope blocked 처리 테스트는 [ADR-0013](../../decisions/ADR-0013-rag-answer-trace-usage-correlation-boundary.md)을 기준으로 유지하고, ADR-0017/implementation baseline matrix 테스트는 target cutover/source-managed/auto/multi-KB mode에 추가한다.

## Audit, Trace, And Privacy Tests

- Successful retrieval audit은 redaction-safe KB/document version/chunk id, score summary, correlation id, policy-safe metadata만 저장한다.
- Hidden/denied/resource-hidden path audit/trace metadata에는 raw title/path/url, exact hidden count, denied KB id, raw source ACL, raw exception을 포함하지 않는다.
- KB/document response projection test fixture와 failure log는 credential-like value나 raw payload를 출력하지 않는다. Encrypted/source config key가 응답, error, audit, trace, captured log에 나타나지 않는지만 구조적으로 검증한다.
- Partial result audit/trace는 safe partial marker, bucketed reason/retryability summary, request/correlation id만 저장한다.
- RAG strategy summary는 `retrieval_strategy`, `rag_mode`, selected collection/KB count, retrieved chunk count, citation count, context token estimate, retrieval latency, permission filter flag, policy result, partial result, safe exclusion summary, query rewrite 적용 여부, evidence sufficiency 결과만 포함한다.
- Skill usage summary는 workflow draft, LLM node의 RAG 옵션, workflow test run, RAG strategy comparison에서 skill id, skill version, freshness state, eval status, safe source tier, safe provenance refs만 포함한다.
- Skill provenance summary는 raw skill body, raw source title/path/url, hidden source refs, exact hidden/denied count를 포함하지 않는다.
- Trace side panel은 raw chunk content, raw source title/path/url, hidden document name/id, exact denied count, raw prompt/completion, provider raw response를 표시하지 않는다.
- Source ACL mapping audit은 safe principal reference만 저장한다.
- Raw content access는 별도 raw/compliance permission을 요구하고 audit을 남긴다.
- Raw content access는 active organization, KB visibility, source-managed KB의 fresh source ACL, retention/legal-hold/purge state를 확인하고, content 반환 전에 access audit을 기록한다.
- Raw content는 Agent answer, SSE stream, retrieval context, embedding input, prompt construction, citation summary, audit metadata, trace metadata, usage metadata, router input, log에 나타나지 않는다.
- Deleted/archived KB metadata에 대한 admin default view는 redacted 상태이며 raw content access를 암시하지 않는다.
- `rag_answer_runs`는 standalone answer anchor로 유지하고 trace/usage table에는 RAG-specific FK column을 추가하지 않는다.
- RAG answer lifecycle 상태 변경, `rag.answer.*` audit, LLM usage row 기록 중 일부가 실패하면 reconcile 또는 transactional outbox 기준에 따라 누락을 감지할 수 있다.
- Vector/keyword retrieval은 `organization_id + knowledge_base_id + active_document_version_id` 또는 동등한 tenant-scoped version namespace 없이 실행되지 않는다.
- RAG answer retention purge는 `completed`, `failed`, `cancelled`, `blocked` 같은 terminal status만 대상으로 삼고 `requested`/`running` row를 삭제하지 않는다.
- 동시 retention purge worker는 같은 answer run을 중복 삭제하거나 중복 purge audit count로 기록하지 않는다.
- Retention purge dry-run은 실제 `purged_count`가 아니라 `would_purge_count` 같은 safe preview 의미로만 표시한다.

## API And UI Tests

- Manual Collection CRUD API는 organization manager 또는 domain `catalog_manage`만 private Collection을 생성할 수 있게 하고, delegated create가 public metadata를 보내도 private로 저장한다. Collection content `read`가 없는 domain 관리자는 허용 action 수행에 필요한 safe 관리 projection만 받는다.
- 실제 PostgreSQL permission row를 사용하는 회귀 테스트는 active User direct grant와 active Team membership grant 각각에서 `catalog_manage`가 domain/list create capability와 POST create에 일관되게 반영되는지 검증한다. 생성 결과는 private/manual/active이고 canonical audit와 같은 transaction에 저장되며 Collection/KB content permission이나 route grant를 자동 생성하지 않아야 한다. 만료·inactive·cross-organization grant는 같은 경로에서 fail-closed해야 한다.
- Collection 관리 Client는 list management projection과 domain capability가 불일치해도 `domain-capabilities.can_create_collection`과 `can_change_public_visibility`를 사용해야 한다. Capability refresh 실패 뒤 이전 create/public control이 남거나, `catalog_manage`가 public visibility control을 활성화하면 테스트 실패다.
- Manual Collection 관리 UI는 생성·편집 가능한 Collection에 관리용 `name`과 별도의 nonblank 안전 표시 이름을 요구하고 request의 `safe_metadata.safe_label`로 전송해야 한다. Gateway는 제공된 label을 string으로 제한하고 공통 sanitizer와 255자 cap을 적용하며 control character·secret-like text·URL·email·path를 제거해야 한다. 타입이 잘못되거나 정제 후 안전 텍스트가 남지 않으면 입력값을 echo하지 않는 `400 validation.failed`여야 한다. Update는 기존 허용 safe metadata와 DB-owned visibility를 보존해야 한다.
- `safe_metadata.safe_label`이 없는 기존 Manual Collection은 관리 edit surface에 보완 필요 상태로 표시하고 raw `name`을 자동 복사하지 않아야 한다. 보완 전 route-safe picker는 `safe_label=null`을 반환하고 Client는 generic `지식 Collection`만 표시해야 한다. 서로 다른 안전 표시명을 저장한 route-allowed Collection은 LLM node picker에서 각각 구분되어야 하며 label 유무가 `route` 또는 child KB `use` 권한을 만들지 않아야 한다.
- Collection update/archive는 resource `manage`, 해당 domain action, 또는 organization manager를 허용하고 system-managed Collection의 source-owned field는 manual update로 바꾸지 못한다.
- Duplicate Collection safe name은 raw DB constraint나 internal value 없이 safe conflict response로 닫힌다.
- Collection item link는 `collection.manage`와 대상 KB `manage`를 모두 요구한다. 둘 중 하나만 있으면 실패하고 hidden KB id/name을 오류에 포함하지 않는다.
- Domain `catalog_manage` actor는 content 권한 없이 private Collection membership을 관리할 수 있다. Public Collection link/unlink/reorder는 domain/resource manage만으로는 실패하고 Organization manager acknowledgement와 source public approval gate를 요구한다.
- Active Collection Privacy Policy Binding이 있는 private item은 `catalog_manage`, `collection.manage` 또는 KB `manage`만으로 unlink할 수 없다. Response는 safe `knowledge_collection_privacy_binding_active`와 `remove_privacy_binding_before_unlink`만 반환하고 membership/audit/validity epoch은 불변이다. Organization manager가 exact impact/expected-revision flow로 binding을 제거한 뒤 fresh revision에서만 unlink할 수 있다.
- Active privacy binding이 있는 Collection을 `lifecycle_manage` 또는 resource `manage`로 archive/restore해도 binding/effective privacy scope/Organization validity epoch은 유지된다. Archived 상태에서 binding은 계속 적용되며 hard delete, referenced-policy purge와 implicit cascade는 `knowledge_collection_privacy_binding_active`로 zero-write다.
- Domain `catalog_manage`만 가진 actor의 Collection item/link-candidate response는 manual KB에 유효한 `safe_metadata.safe_label`이 있으면 sanitizer를 통과한 label만 표시하고, 없으면 generic label을 표시한다. Raw `kb.name`은 독립 KB `read`가 확인된 actor에게만 허용하며 source-managed KB는 기존 display-policy-approved safe label 규칙을 계속 적용한다.
- Collection item duplicate link는 idempotent success 또는 문서화된 safe conflict 중 하나로 deterministic하게 처리한다.
- Collection item unlink와 reorder는 같은 Collection 안의 item만 대상으로 하며, 다른 organization 또는 hidden KB item을 조작하지 못한다.
- Link candidate API는 resource manager에게 KB `manage` 가능한 후보만 반환하고, domain `catalog_manage`에는 private membership 관리용 safe 후보만 반환한다. Public 후보는 Organization manager와 source exposure gate를 통과해야 한다.
- Collection permission grant/revoke는 `read`, `route`, `manage`, `sync`만 허용하고 explicit deny나 role inheritance를 만들지 않는다.
- Collection UI role bundle은 Viewer=`read`, Workflow Router=`read+route`, Maintainer=`read+manage`, Sync Operator=`read+sync` explicit row를 한 transaction에서 적용하며 어떤 bundle도 KB `use`를 만들지 않는다.
- Collection permission revoke는 자기 자신의 마지막 `manage` grant 제거 edge case를 safe denial 또는 organization manager 전용 동작으로 처리한다.
- KB permission list API는 `resource_type="knowledge_base"`와 team/user permission 목록을 반환하고, hidden KB id/name/count를 노출하지 않는다.
- KB team permission grant/revoke는 기존 team KB permission table을 사용하고, KB user direct permission grant/revoke는 `user_knowledge_permissions`를 사용한다.
- KB team/user permission grant/revoke는 권한 row 변경과 같은 transaction에서 canonical data-change audit row를 하나만 추가하며, Core upsert 또는 bulk delete가 ORM listener를 우회해도 audit이 누락되지 않는다.
- Resource permission registry contract는 schema가 허용하는 `workflow`, `llm_credential`, `knowledge_base` resource type과 Gateway routing key가 일치하는지 검증한다.
- `resource_type="knowledge_base"` grant/revoke/list는 `TeamKnowledgePermission`과 `UserKnowledgePermission`만 사용하고 LLM credential 또는 workflow permission fallback으로 흐르지 않는다.
- KB hard delete는 Knowledge lifecycle service boundary를 통과하고, `team_knowledge_permissions`, `user_knowledge_permissions` direct grant row를 같은 transaction에서 먼저 정리해 orphan permission이나 FK failure를 남기지 않는다. Disposable PostgreSQL integration은 owner predicate, wrong-owner no-mutation, legacy cross-organization permission cleanup, document/chunk cascade와 실제 FK delete 성공을 검증한다.
- KB hard delete는 Organization manager와 explicit acknowledgement만 허용한다. Resource manager와 domain lifecycle manager는 archive/restore만 수행하고 hard delete나 system-managed source-owned lifecycle을 수행하지 못한다. Permission cleanup, canonical audit, KB delete 중 하나라도 실패하면 DB mutation 전체를 rollback한다.
- Unknown/cross-org/invisible direct resource는 `404 resource.hidden`, same-scope visible action 부족은 `403 permission.denied`, 목록은 unauthorized row와 exact hidden count를 생략한다.
- Disposable PostgreSQL integration은 명시적 host/port/user/password를 요구하고 기본 credential을 사용하지 않는다. Loopback 밖 host는 exact host confirmation 없이는 연결하지 않으며, allowlist random DB name만 생성/삭제하고 subprocess에는 검증된 개별 DB 설정만 전달한다. 실패 출력과 config representation은 credential/connection detail을 노출하지 않는다.
- Runtime/builder bulk KB permission evaluation은 team KB permission과 `user_knowledge_permissions` direct grant를 모두 합산해야 한다. User direct grant만 있는 경우에도 해당 user의 KB `use` 권한이 허용되어야 한다.
- Conversation Memory authorization adapter는 decision/principal kind/authorization decision revision, KB lifecycle/resource, policy revision과 evaluated timestamp를 각 bulk result에 포함하고 source-managed KB의 source ACL revision을 decision revision에 반영한다.
- Bulk result 일부가 누락되거나 revision을 만들 수 없으면 해당 dependency를 `unknown`으로 반환하고 allow로 채우지 않는다.
- Permission revoke, membership state 변경, KB archive/delete와 source ACL version 변경은 관련 revision을 바꾸어 기존 Memory lease/context 재사용을 차단한다.
- Public audience는 synthetic subject 없이 `anonymous_public_audience`로 평가하고 login cookie/Conversation Access Grant를 private KB permission으로 사용하지 않는다.
- Knowledge retrieval result는 KB/document version, organization, sensitivity와 authorization-safe reference만 RuntimeDataDependencyEnvelope로 반환하고 raw title/path/URL/content/ACL을 포함하지 않는다.
- Client/node가 Knowledge dependency를 위조하거나 optional로 낮춰도 canonical envelope를 변경하지 못한다. Answer에 영향을 준 KB dependency 하나가 revoke되면 derived Memory entry 전체를 제외한다.
- Public visibility 전환은 organization manager와 explicit acknowledgement를 요구하고, 전환 audit에는 raw KB title/path/url, hidden KB id/name, exact denied count가 들어가지 않는다.
- Public visibility가 켜져도 인증 사용자 KB `use` 권한이나 source ACL requester authorization이 생기지 않는다.
- Source-managed KB는 collection public flag만으로 anonymous public-only 후보가 되지 않는다. Source/connector public exposure approval이 없거나 `approval_scope`와 target field가 맞지 않는 approval row만 있으면 후보에서 제외된다.
- MBA-176 preflight/runtime availability에서 source-managed KB public exposure approval primitive가 없으면 `source_public_exposure_required` blocked로 처리하고 warning으로 낮추지 않는다.
- Connector-wide public exposure approval은 expiry, reverification cadence, revocation behavior, explicit acknowledgement가 없으면 invalid policy로 처리된다.
- Knowledge Collection 관리 UI는 Workflow Builder와 분리되어 있고, Builder 화면에서 Collection 생성/삭제/권한관리를 주 기능으로 제공하지 않는다.
- Collection 관리 UI는 `can_manage_collection`, `can_manage_kb`, `can_use_kb`를 혼동하지 않고, item list에 보이는 KB가 runtime retrieval 가능성을 보장하지 않는다는 상태를 표현한다.
- Collection 관리 UI는 raw source title/path/url/principal, hidden KB name/id, exact denied count를 표시하지 않는다.
- Collection list는 safe redacted name/description과 non-color text label이 있는 state badge를 표시한다.
- Collection lifecycle list는 active와 archived query를 분리한다. Archived manual Collection restore는 Organization manager, Collection `manage`, domain `lifecycle_manage`에서 허용하고 active restore retry는 새 mutation/audit 없는 `204`여야 한다. Deleted/cross-organization 대상은 hidden, system-managed 대상은 source-owner policy denial, `source_deleted` 대상은 safe conflict로 처리한다.
- Archive와 restore는 실제 PostgreSQL에서 같은 Collection row를 `FOR UPDATE`로 잠근다. 두 concurrent restore는 상태 전이와 canonical audit를 한 번만 만들고 audit flush/commit 실패는 lifecycle mutation과 audit를 모두 rollback한다. Restore가 permission, membership, child KB `use` 또는 Workflow `route` row를 만들면 테스트 실패다.
- Item GET, link와 reorder success response는 최신 전체 ordered item projection, `order_revision`, `reorder_supported`, optional fixed `safe_reason_code`를 같은 의미로 반환한다. Unlink 204 뒤 GET revision은 이전 값과 달라야 한다.
- Reorder는 current 전체 item id set, unique item id, unique contiguous `0..N-1` rank와 `expected_order_revision`을 요구한다. Duplicate item overwrite, partial request, missing/foreign/extra item, duplicate/gapped/negative rank, malformed 또는 다른 Collection revision은 아무 mutation 없이 거부한다. Current order no-op에는 새 audit를 만들지 않는다. Deleted/cross-organization target은 authority 또는 item order 조회 전에 hidden `404`로 처리한다.
- Item 0개와 1개는 stable revision을 만들 수 있고 500개는 reorder 가능하다. 501개부터 `reorder_supported=false`, `safe_reason_code=item_reorder_limit_exceeded`이며 mutation을 허용하지 않는다. Token은 권한이나 item 조회 capability로 사용되지 않는다.
- Empty Collection reorder는 `items=[]`와 current revision을 수용해 no-op safe projection을 반환한다. Link의 legacy `rank` 값은 삽입 위치를 바꾸지 않고 새 item은 끝에 append되며 전체 rank가 연속값으로 정규화되어야 한다.
- Link/reorder mutation authority는 있지만 별도 Collection `read`가 없는 actor도 mutation commit 뒤 safe management projection을 받아야 한다. 성공한 DB mutation 뒤 response projection의 권한 오류로 실패 응답을 반환하면 테스트 실패다.
- PostgreSQL concurrent reorder/reorder는 먼저 commit한 한 요청만 성공하고 두 번째는 lock 뒤 stale conflict가 된다. Reorder/link, reorder/unlink, reorder/visibility도 같은 Collection-first lock protocol을 사용해 membership set, rank, public acknowledgement가 stale 판단으로 우회되지 않아야 한다. 최종 rank는 contiguous하고 audit failure는 전체 rank를 rollback한다.
- Public Collection/KB의 source identity뿐 아니라 Collection `source_connector_ref`도 public link/reorder/visibility에서 같은 `source_public_exposure_required` fail-closed 정책을 적용한다.
- Delegation subject endpoint는 authority 확인 전 Team/User SELECT를 실행하지 않는다. Collection endpoint는 Organization manager, Collection `manage`, domain `permission_delegate`; domain endpoint는 Organization manager를 먼저 검증한다.
- Subject page는 `subject_type=team|user`, safe prefix query 100자 이하, opaque cursor, 기본 limit 25/최대 50을 적용한다. Current organization active Team과 active member User만 반환하고 inactive/cross-organization row, email, login principal, raw source identity와 total count를 포함하지 않는다.
- Subject prefix search는 whitespace와 case를 일관되게 처리하고 `%`, `_`, quote를 SQL wildcard/injection으로 해석하지 않는다. 동일 safe label은 UUID keyset tie-break로 page 간 duplicate/skip 없이 반환한다. Subject type/query가 다른 cursor와 malformed/oversized cursor는 입력값을 echo하지 않는 bounded safe error다.
- Subject combobox는 Team을 기본값으로 두고 debounce, loading, empty, error/retry, next-page, stale response 폐기와 keyboard navigation을 제공한다. Type/query 변경 시 stale selection을 정리하고 email/raw UUID를 visible label fallback으로 사용하지 않는다.
- Bundle revoke는 Viewer=`read`, Workflow Router=`read+route`, Maintainer=`read+manage`, Sync Operator=`read+sync`의 현재 explicit row만 한 transaction에서 제거한다. 없는 row는 unchanged이며 Maintainer grant 뒤 Viewer revoke 결과는 `manage`만 남는다. UI/API가 이를 저장된 Maintainer role로 추론하면 테스트 실패다.
- Bundle grant/revoke 일부 row 또는 audit 저장 실패는 전체 rollback한다. Grant는 active subject만 허용하고 inactive Team/removed User의 기존 permission은 revoke할 수 있다. 어떤 bundle도 child KB `use`를 만들지 않는다.
- Domain `permission_delegate` actor의 self/own-active-Team bundle grant는 차단하고 revoke는 last-manage 검증 뒤 허용한다. Current actor의 마지막 `manage` 경로를 제거하는 single/bundle/bulk revoke는 Organization manager recovery가 아닌 경우 전체 거부하며 independent manage path가 있으면 허용한다.
- 모든 bulk target에 effective Collection `manage`가 있는 actor는 domain `permission_delegate`도 함께 보유했다는 이유만으로 self/own-Team grant가 차단되지 않는다. 일부 target에만 resource `manage`가 있으면 domain-delegate self-escalation 차단을 적용한다.
- Multi-Collection bulk bundle은 unique Collection id 1~50개와 한 subject/bundle/operation만 받는다. 0개, 51개, duplicate/malformed id와 invalid enum은 mutation 전에 거부한다. Collection을 UUID 순으로 잠그고 모든 target의 organization/authority/last-manage를 사전 검증하며 한 target 실패 시 permission/audit 전체를 rollback한다.
- Bulk response는 operation, subject type, bundle과 target/changed/unchanged count bucket만 포함하고 Collection/subject id, label, 실패 index나 exact hidden count를 반환하지 않는다. 11~50개 count는 `11-50` bucket을 사용하고 response validation은 permission/audit commit 전에 완료한다. 1, 2, 50개 grant/revoke와 retry는 duplicate permission row 없이 deterministic해야 한다.
- PostgreSQL bulk 검증은 cross-organization target 혼합, N-1 authorized + 1 unauthorized, 한 target의 last-manage 실패, 반대 순서 target을 가진 concurrent request와 audit failure를 포함한다. Query/lock capture는 organization predicate와 실제 `FOR UPDATE`를 확인하고 target 수만큼 authorization query가 늘어나는 N+1을 허용하지 않는다.
- Permission UI는 Team row와 User direct row를 분리한다. 같은 action에 두 source가 있을 때 direct revoke 뒤 Team effective allow가 남는 사실을 표시하고 revoke success를 전체 접근 차단으로 잘못 표현하지 않는다.
- MBA-264는 Collection sync 실행 endpoint/job/UI, explicit deny, per-Collection expiry, bundle 저장 row, child KB `use` 자동 grant와 Workflow graph/runtime 변경을 추가하지 않는다.
- KC sync request는 Organization manager, effective Collection `sync`, domain `sync_manage`를 허용하고 Collection `manage`만 있는 actor를 거부한다. Request와 worker claim 양쪽에서 current authority를 평가하며 requested actor identity는 bypass가 아니다.
- Request와 worker authorization은 ADR-0009 manager helper를 active membership gate보다 먼저 평가한다. Membership row가 없는 legacy `created_by`/`managed_by` manager는 양쪽에서 허용되지만, suspended/removed membership row가 있는 legacy owner/manager는 fallback하지 않고 거부되어야 한다.
- KC sync request의 UUID idempotency key는 hash로만 저장하고 같은 organization/Collection/key의 동시 요청은 job 하나만 만든다. 다른 key의 동시 요청도 queued/running partial unique single-flight에 따라 active job 하나만 남기며 terminal 뒤 새 요청은 허용한다.
- Job, deterministic target item, Collection `pending`, requested audit 중 하나라도 실패하면 transaction 전체를 rollback한다. Celery publish는 commit 뒤 호출하고 publish failure는 raw broker detail 없이 queued job으로 복구 가능해야 한다.
- Worker task payload와 result는 job UUID와 safe summary만 포함한다. Active Manual Collection의 active/non-source-managed document-level KB에 있는 단일 DB document만 실행하며, 2개 이상 DB 문서 또는 DB+FILE/API가 한 KB에 섞인 legacy multi-document KB, system/source-managed Collection, source-managed child, API connector sync를 성공처럼 처리하지 않는다.
- Celery duplicate delivery는 unexpired running lease나 terminal job에서 target adapter를 호출하지 않는다. Worker loss 뒤 stale lease recovery는 running item을 bounded retry로 되돌리고, target apply와 item success/progress commit 뒤 발생한 redelivery는 같은 target을 다시 적용하지 않는다.
- Concurrent permission revoke가 worker의 fresh authorization query 전에 commit되면 job은 cancelled되고 target을 실행하지 않는다. Authorization query 뒤 commit된 revoke는 이미 시작한 batch를 중간 중단하지 않고 다음 claim부터 반영한다. 이 순서는 실제 PostgreSQL lock/barrier test로 검증한다.
- Target unlink, KB archive/delete/source-managed 전환, document 삭제/type 변경은 hidden identity를 노출하지 않는 skipped/partial 상태로 처리한다. Job 생성 뒤 membership add/remove/reorder 또는 child source composition 변경은 claim과 finalize에서 ordered membership snapshot을 다시 계산해 `sync.targets_changed`로 닫고, 기존 snapshot을 성공으로 finalize하지 않는다.
- Job item은 요청 시점 per-target revision을 저장한다. Membership UUID/rank/created time 또는 document updated time이 바뀌면 worker가 source/embedding 호출 전에 `sync.targets_changed`로 닫고, live KB/document hard delete 뒤에도 durable item이 cascade 삭제되지 않아 original total과 함께 changed target으로 집계되어야 한다.
- 일부 child의 max retry 실패를 `succeeded`로 표시하지 않는다. Success+failure/changed target은 `partially_failed`와 Collection `stale`, all failure는 `failed`, all success는 `succeeded`와 `synced`로 집계한다.
- Failed/skipped item이 둘 이상이어도 representative safe reason 조회는 position 순 첫 행 하나로 제한되어 다중 행 예외 없이 terminal finalization을 완료한다.
- Item row가 손실되거나 current row count가 immutable job total보다 작으면 누락 수를 `sync.targets_changed` skipped로 보정한다. All-zero current rows를 `succeeded`로 finalize하지 않으며 terminal processed count는 original total과 정확히 일치해야 한다.
- Sync status API/UI는 status, 범주형 progress, allowlisted safe reason, retryability와 timestamp만 사용한다. Exact target/success/failure count, job item, KB/document/source identity, raw connection/config/SQL/credential/payload/exception은 응답, DOM, audit, trace, log에 없어야 한다.
- Collection projection은 caller 권한 `can_sync`와 adapter 능력 `sync_supported`를 분리한다. Projection과 POST는 같은 canonical child eligibility scan을 사용한다. 권한이 없으면 panel/latest polling이 없고, source-managed/API/multi-document child 또는 eligible DB target 부재로 unsupported인 Collection이면 버튼이 disabled이며 실행 API를 호출하지 않는다. Worker requester의 organization authority와 별도로 execution subject가 Connection owner가 아니거나 DB type이 unsupported면 configuration failure로 닫고, stored limit이 1,000을 넘으면 worker-local 실행 config만 1,000으로 제한한다.
- 같은 document에 대한 KC sync와 기존 ingestion chunk replacement는 PostgreSQL transaction advisory lock으로 직렬화한다. Advisory lock은 document/Collection/KB row lock보다 먼저 획득한다. 첫 transaction이 lock을 보유하는 동안 두 번째 writer는 기존 chunk를 삭제하지 못하며, 첫 transaction rollback 뒤 두 번째 writer가 일관된 이전 상태에서 진행하고 commit해야 한다.
- Gateway ingestion과 KC sync 모두 source fetch/parsing/embedding 전에 shared advisory lock을 잡는다. KC sync는 새 version ID에만 chunk를 쓰고 finalizer가 active pointer를 교체해야 하며, 기존 active version/chunk는 성공 전까지 retrieval-visible해야 한다. Empty result, embedding/finalization 실패와 rollback은 active pointer를 바꾸지 않는다.
- KC sync는 DB processor의 전체 chunk를 바로 저장하지 않고 문서에 저장된 flat `range`/`keyword` selection을 기존 ingestion과 같은 helper로 적용한다. 선택 밖 chunk가 새 version에 들어가면 실패이며, empty selection과 malformed selection은 새 version을 만들거나 active pointer를 바꾸지 않는다.
- Legacy `document_version_id=None` vector save는 같은 document의 NULL-version chunk만 조회·삭제하고 active/historical version chunk를 보존한다. Versioned chunk의 embedding을 unversioned incremental reuse source로 사용하지 않는다.
- Job 실행 중 Collection이 `source_deleted`가 되면 cancel/finalize/queue/recovery가 `pending`, `syncing`, `synced`, `stale`, `failed` 또는 previous state로 덮어쓰지 않는 것을 repository 상태 전이 test로 검증한다.
- Target 100개 job은 정상 batch claim 20회에 item retry 또는 stale recovery가 한 번 추가되어도 job-attempt 상한 때문에 조기 실패하지 않는다. Claim budget은 target 수의 batch, item 최대 retry와 stale recovery 5회를 반영하고 overall deadline은 별도로 적용한다.
- Item attempt는 `running` 표시가 아니라 succeeded/failed/skipped outcome을 영속하는 transaction에서 정확히 한 번 증가한다. 일시 실패 3회의 영속 attempt는 `1, 2, 3`이고 `max_attempts=3`이면 세 번째 실제 실행 뒤에만 terminal failed가 된다.
- DB source query builder는 공백·대소문자·인용부호가 있는 PostgreSQL identifier를 안전하게 인용한다. Stored table/column 값에 JOIN/subquery 형태의 text가 있더라도 별도 SQL fragment로 실행하지 않으며, JOIN은 선택되지 않은 table을 참조할 수 없고 non-integer·0·상한 초과 `LIMIT`은 connector 호출 전에 거부한다.
- 사용자 A가 사용자 B의 Connection UUID를 DB source upload/process/preview에 제출하면 document/설정 mutation과 background 등록 전에 `404 resource.hidden`으로 닫는다. Missing/malformed UUID와 deleted Connection도 같은 외부 shape를 사용하고 Connection 상세를 포함하지 않는다.
- 정상 owner가 저장한 DB source라도 background 실행 전에 Connection이 삭제되거나 owner가 변경되면 Gateway ingestion, direct `DbProcessor`와 KC sync가 adapter 생성·DB dial 전에 configuration failure로 닫혀야 한다. Gateway 검증 결과를 background capability로 재사용하지 않는다.
- Connection resolver의 SQLAlchemy/driver 조회 실패는 raw detail이나 500으로 새지 않고 Gateway `503 connection.reference_unavailable`, processor `source.temporarily_unavailable`로 정규화하며 adapter를 호출하지 않는다. Preview의 두 번째 runtime 판정에서 발생한 `resource.hidden`과 temporary failure도 각각 404/503 의미를 유지하고 일반 400으로 축소하지 않는다.
- Disposable PostgreSQL 검증은 같은 Session에 이미 로드된 Connection의 owner가 다른 transaction에서 변경된 뒤 다음 resolver가 stale identity를 재사용하지 않고 old owner를 거부하는지 확인한다. MBA-281은 runtime row lock을 검증하지 않으며 lock mode/order/scope test는 MBA-302로 분리한다.
- MBA-302 runtime snapshot 검증은 owner predicate와 credential projection을 독립 session에서 완료하고 session을 닫은 뒤에만 Gateway/Workflow/KC connector가 호출되는지 확인한다. Success, hidden, configuration/store failure와 connector cancellation 모두 snapshot transaction/session을 남기지 않으며 processor output/repr에는 Connection identity와 credential이 없어야 한다. Stored database/username의 앞뒤 공백은 임의 trim하지 않고 encrypted SSH password가 없는 기존 password-auth row는 불필요한 decrypt 없이 `password=None`으로 투영한다.
- Connection reference writer가 owner row lock을 보유한 동안 delete/update contender는 PostgreSQL local 2초 안에 safe retryable busy로 종료하고 전체 rollback해야 한다. Writer commit 뒤 새 transaction은 committed Document reference를 확인해 delete를 `connection.in_use`로 막고, 최초 조회 revision보다 Document가 갱신되었으면 stale writer를 `connection.reference_conflict`로 막아야 한다. Deadlock victim은 같은 session의 부분 상태를 재사용하지 않아야 한다. Metadata commit에서 `40001`/`40P01`/`55P03`/`57014`가 발생해도 `connection.reference_busy`, 기타 SQLAlchemy commit 오류는 `connection.reference_unavailable`로 닫고 background task를 등록하지 않아야 한다.
- Runtime PostgreSQL fetch는 connect 5초, statement 5초, bounded batch, 총 10,000 row와 총 16 MiB cap을 적용한다. Row/byte limit 초과는 일부 chunk를 success로 finalize하지 않고 generator exception/cancel에서 engine/tunnel을 정리해야 한다.
- DB source 저장 metadata는 top-level opaque `connection_id`만 Connection reference로 유지하고 client가 보낸 host/username/password, legacy `connection_name`/`db_type`과 encrypted/decrypted credential을 제거한다. Processor success/failure metadata와 chunk source label도 Connection id/name/host/user/credential을 포함하지 않는다.
- DB/SSH credential 복호화 실패 시 저장 암호문을 adapter에 전달하지 않고 connector fetch가 0회인지 검증한다. Audit와 document failure에는 safe error class만 남는다.
- Client는 한 사용자 동작에서 같은 idempotency key를 재사용하고 queued/running 중 double submit을 막는다. Latest/specific polling은 3초 간격으로 terminal/unmount/Collection 변경 시 중단하고 이전 Collection의 늦은 response를 폐기한다.
- Sync request preflight의 `sync.no_eligible_targets`, `sync.target_limit_exceeded`, `sync.not_supported`는 safe 409 error code로 구분하고 unknown reason은 raw value를 반사하지 않는 `policy.blocked`/`sync.internal_error`로 일반화한다.
- Helm template은 정확히 한 replica와 `Recreate` strategy를 가진 별도 Celery Beat Deployment를 렌더하고 shared Celery app schedule이 `workflow` queue의 KC recovery task를 발행할 수 있는 Redis broker 설정을 주입한다. `beat.enabled=false`에서는 해당 Deployment를 렌더하지 않는다.
- Disposable PostgreSQL test는 active-job partial unique index, organization predicate, exactly-one claim, `SKIP LOCKED` recovery, audit rollback, concurrent revoke linearization과 terminal retention cleanup을 검증한다. Fake `.filter()`가 predicate를 버리는 test로 대체하지 않는다.
- Source metadata에서 유래한 system-managed collection display name/description은 storage/display 전에 redaction, cap, display-policy approval을 거친다.
- KB detail은 hidden source path를 누출하지 않으면서 sync failed, source ACL stale, source deleted, archived, deleted state를 구분한다.
- Remediation queue는 raw connector exception string이 아니라 safe reason code와 retryability를 표시한다.
- Citation은 target identity field(`citation_id`, `knowledge_base_id`, `document_version_id`, `chunk_id`, optional `collection_id`, optional `safe_source_ref`)를 사용한다. `safe_source_ref`는 protected/HMAC source reference이며 raw source id/url/path/principal을 대체한다.
- User-facing content preview는 redacted/capped 상태이며 durable audit/trace/usage summary에 복사하지 않는다.
- A/B 비교 UI는 권한 없는 문서명/ID를 표시하지 않고 authorized evidence 기준의 context token, retrieved chunk count, citation count, cost, latency, quality score만 비교한다.
- 향후 Skill management, Workflow Playground, Agent Builder skill-binding UI가 추가되면 safe skill metadata, freshness, eval status, publication/review 상태만 표시하고 hidden source title/path/url이나 raw skill resource를 표시하지 않는다.
- Workflow Playground에서 draft/unpublished skill 실험을 허용하는 정책을 채택하더라도, 운영 실행 시점 자동 후보에는 포함되지 않고 actor의 KB permission/source ACL/redaction gate를 우회하지 않는다.
- Demo fixture는 상담원 허용 문서와 제한 문서를 분리하고, 제한 문서가 모든 운영 RAG mode의 prompt/citation/trace에 포함되지 않는지 검증한다.

## Performance And Load Tests

### MBA-301 Retrieval Embedding Model Batch Projection

- Authorized KB 1개와 상한 20개, 동일 model과 mixed model 모두에서 implicit retrieval의 `LLMModel` query 상한은 1회로 유지되어야 한다.
- Explicit verified model과 authorized candidate 0건은 model projection query를 실행하지 않아야 한다.
- Query count는 회귀 차단 기준이며, 실제 PostgreSQL 환경에서는 변경 전 per-KB lookup 대비 query 수와 latency를 관찰한다. 환경 변동이 큰 단일 latency threshold는 merge gate로 사용하지 않는다.
- MBA-289가 Authorized Retrieval Port를 도입할 때 projection은 권한 판정을 복제하지 않고 authorized candidate 이후 adapter 내부 단계로 이동해야 한다.

### MBA-354 Precomputed Query Embedding KB Fanout

- Authorized candidate가 0개면 query embedding, fanout scheduler, child DB session과 generation provider를 모두 호출하지 않는다. Candidate가 있으면 ADR-0071의 distinct-model capability/provider attempt와 invocation-local vector를 그대로 소비하며 KB별 embedding을 다시 만들지 않는다.
- 사전 계산 query vector를 사용하는 2개 이상 KB search는 실제 native worker에서 겹쳐 실행되고 active worker는 5개를 넘지 않는다. Barrier 기반 test로 overlap을 검증하며 우연한 wall-clock 단축만 성공 기준으로 사용하지 않는다.
- Aggregate deadline은 executor/task 제출 전에 시작한다. Queue 대기, active search, cancellation과 cleanup 대기를 포함해 caller 기준 30초를 넘기지 않고 마지막 1초에는 새 DB search를 시작하지 않는다. Cancellation에 협조하지 않는 worker를 기다리느라 caller deadline을 연장하지 않으며 late result를 폐기하는 test를 포함한다.
- 각 worker는 별도 session에서 `organization_id + knowledge_base_id` 조건, read-only transaction과 남은 budget 이하의 transaction-local statement timeout을 적용한다. Success, empty, DB error, per-KB timeout, aggregate cancel과 rollback error 모두 close를 시도한다. Disposable PostgreSQL test는 `pg_sleep` cancel 뒤 rollback, connection 재사용과 timeout 설정 비누출을 확인한다.
- Reverse completion, partial timeout과 mixed success에서도 candidate ordinal을 거쳐 기존 global score/source-tier 정렬, dedupe, top-k, evidence sufficiency와 citation 결과가 순차 기준 fixture와 같아야 한다. `fail_node`는 partial evidence를 사용하지 않고 남은 task에 cancellation을 요청한다.
- RetrievalService의 sync/async exception log와 fanout error projection에는 raw query, SQL/parameter, KB/document/chunk identity, provider payload와 raw exception 문자열이 없어야 한다. `fail_node`도 실패 정책은 유지하지만 child 원문 예외 대신 고정된 safe fanout error를 반환한다.
- Authorization된 RAG trace에는 실행된 stage의 `candidate_resolution_latency_ms`, `query_embedding_latency_ms`, `retrieval_fanout_latency_ms`, `slowest_search_latency_ms`, `evidence_policy_latency_ms`만 0~300,000 범위 integer로 남긴다. Unknown/negative/non-finite/bool/per-KB timing은 제거하고 일반 Workflow result metadata, chatbot/SSE와 citation에는 이 필드가 없어야 한다.
- Synthetic benchmark는 KB 1/2/4/10개에서 동일 delay profile의 p50/p95, max active worker와 search/provider call count를 기록한다. 절대 latency threshold는 merge gate가 아니며 overlap, concurrency, 호출 수와 deterministic 결과를 회귀 gate로 사용한다. Slow/timeout 동작은 별도의 deterministic scheduler test로 검증한다.

### MBA-288 Durable Document Ingestion

- Process/sync/resume/reindex admission은 Document 또는 KB row lock 아래 설정·queued projection·job을 한 transaction에 저장한다. DB source process/sync는 owner Connection lock 뒤 fresh KB/Document lock과 revision compare를 수행하고 같은 lifecycle UoW로 reference metadata와 job을 commit한다. Approval resume의 첫 요청이 Document를 `waiting_for_approval`에서 `indexing`으로 바꾼 뒤 같은 요청을 반복해도 active 동일 intent를 재사용하며, 다른 intent만 conflict로 닫는다. Commit 실패에는 job과 Document 변경이 모두 없고, commit 뒤 broker publish 실패에는 pending job이 남아 recovery로 실행 가능해야 한다.
- 동일 Document의 concurrent request는 실제 PostgreSQL partial unique와 row lock에서 active job 하나만 남긴다. Same intent는 같은 job을 반환하고 다른 intent 및 partial KB reindex conflict는 전체 rollback한다.
- Worker duplicate delivery, future retry, valid lease와 terminal job은 parser/provider를 호출하지 않는다. Claim winner 하나만 runner에 진입하고 owner/fencing token이 다르거나 PostgreSQL actual wall-clock execution lease가 만료된 heartbeat, worker lock, progress, failure와 finalization은 거부된다. 장기 transaction에서 `now()`는 고정되지만 `clock_timestamp()` 기반 finalization은 실제 만료를 거부해야 한다.
- Soft time limit과 allowlisted transient DB/API/remote FILE egress failure는 bounded `retry_scheduled`로, unknown/permanent failure는 safe dead-letter로 전환한다. FILE processor부터 runner까지 typed reason을 보존하며 timeout, connection, DNS, API 408/425/429/5xx는 retry되고 private target 등 egress 보안 차단과 설정 오류는 retry되지 않아야 한다. Retry task는 `next_retry_at` 전에 즉시 재발행하지 않고 due recovery가 발행한다. 최대 attempt 뒤 자동 실행은 없다.
- Crash/lease expiry recovery는 running owner/fence를 무효화하고 retry 또는 dead-letter로 전환한다. `pending|retry_scheduled` 발행 전에 bounded dispatch lease를 기록하며 recovery를 즉시 다시 실행해도 같은 job을 재발행하지 않아야 한다. Late heartbeat와 finalizer가 새 generation의 Document progress/active pointer/job result를 덮지 못해야 한다.
- HTTP admission과 최초 worker claim, Recovery와 worker finalization/failure transition이 경합하는 disposable PostgreSQL 테스트는 `KnowledgeBase -> Document -> job` 순서를 지켜 deadlock 없이 끝나고 만료된 finalizer가 성공하지 못함을 검증한다. 첫 due scope가 다른 worker에게 잠겼으면 recovery는 3초 안에 이를 건너뛰어 이후 due job을 처리하고 잠금 해제 뒤 첫 job을 복구해야 한다.
- Active version swap, Document completed와 job succeeded는 같은 transaction에서 확정한다. Parse/chunk/embed/finalization 실패와 rollback은 이전 active ready version을 유지하며 pre-finalized chunk를 retrieval에 노출하지 않는다. Unchanged no-op은 active ready version의 `legacy_document_id`가 현재 Document와 일치할 때만 성공하고 다른 legacy Document pointer면 재색인한다. Chunk 저장 단계 progress는 current owner/fence와 actual wall-clock lease를 짧은 DB 조회로 확인한 뒤 최대 99를 발행하며 SSE 종료 조건인 100은 성공 commit 뒤에만 발행한다.
- Worker-start authorization은 current organization membership과 KB write 또는 sync authority를 재검사한다. Revoke가 authorization query 전에 commit되면 source/provider 호출 없이 cancel하고, hidden resource identity는 status/log에 노출하지 않는다.
- Status와 SSE는 owner/fencing token, input revision, idempotency key, raw source config/path/content, provider exception을 반환하지 않는다. Status는 `no-store`이고 unknown reason은 allowlisted generic code로 축소한다. 새 process/reindex/redrive admission과 retry/cancel/dead-letter/recovery commit은 이전 attempt의 Redis progress key를 삭제하고 SSE는 새 attempt 또는 DB의 0 projection으로 fallback해야 하며 lease-lost worker는 key를 삭제하지 않는다.
- Missing table/column/idempotency unique/active partial unique/due·lease index는 worker startup과 API readiness에서 fail-closed한다. Processing Document 상세 조회도 authorization 뒤 reconciliation query 전에 readiness를 검사해 `503 knowledge.ingestion_schema_not_ready`를 반환하고 raw introspection 오류를 반사하지 않는다.
- Docker Compose와 Helm rendering은 Gateway image 기반 `knowledge` queue 전용 worker, bounded concurrency/prefetch, recovery beat route와 migration-first startup gate를 검증한다. 기본 Helm values는 worker opt-in을 유지하지만 provider-neutral production reference는 worker와 singleton Beat를 함께 활성화하고 worker-only, replica 0 또는 concurrency 0 설정을 safe render error로 거부해야 한다. Compose worker는 Gateway health 이후 시작하고 Helm worker는 bounded schema init readiness를 통과해야 한다. 실행 중 readiness는 `knowledge@<pod-hostname>` exact destination에 bounded ping을 보내 local pong만 허용하고 empty/malformed/다른 replica 응답과 broker exception을 raw detail 없이 실패 처리해야 한다. Kubernetes probe timeout은 내부 ping timeout보다 길고, Redis/control path 장애를 반복 restart로 바꾸는 liveness probe는 두지 않는다. Helm이 ServiceAccount를 생성하도록 설정되면 worker가 참조하는 같은 이름의 리소스가 렌더링되고 외부 ServiceAccount 설정에서는 생성되지 않아야 한다. `LOCAL` mode는 Gateway/worker가 같은 PVC와 non-root write를 위한 fsGroup을 사용하며 shared storage가 없으면 rendering이 실패하고 `CLOUD` mode에는 Knowledge upload PVC가 없어야 한다. 다른 queue worker는 Knowledge table readiness에 결합되지 않는다.
- Migration은 최신 dev 기준 Alembic single head를 유지하고 실제 disposable PostgreSQL에서 upgrade, active-job unique, concurrent claim, heartbeat fencing과 terminal cleanup을 검증한다.

- Bulk permission helper는 per-KB database query 없이 user-candidate lookup과 KB-centric lookup을 처리한다.
- Candidate cap은 stable ordering으로 큰 candidate set을 deterministic하게 잘라낸다.
- MBA-232 runtime candidate ID/authorization은 invocation 사이에 cache하지 않는다. 향후 별도 승인된 candidate cache는 permission/freshness revision을 포함하고 ACL revocation 시 invalidation되어야 한다.
- Runtime access cache key는 mapping epoch와 source ACL freshness epoch를 포함하고, source item/document version 단위로 분리된다.
- Skill candidate cache key는 skill version, freshness state, eval state, source version reference를 포함하고 stale skill/source-tier 변경 시 invalidation된다.
- 단일 filtered vector/keyword query를 우선한다. Bounded fanout을 사용하면 concurrency와 timeout cap을 강제한다.
- Query rewrite cache key는 rewrite mode, safe template id, skill version, permission/freshness epoch를 포함하고 raw rewritten query를 durable key로 사용하지 않는다.
- Retry/dead-letter는 idempotency key, retryable flag, attempt count, safe reason code, dead-letter state, re-drive path를 검증한다.
- Load test는 max candidate KB, max route collection, max retrieval KB, max chunks per KB, max total chunks, fanout timeout, aggregate interactive timeout, permission helper index, candidate cache, recovery scanner cadence, trace/audit payload size, partial operational failure behavior, query rewrite 추가 latency/cost budget을 포함한다.
- Concurrent ingestion, concurrent DB-source sync, concurrent retention purge, cleanup outbox retry의 race 테스트를 포함한다.

## Workflow User Citation Tests

- `hidden`, `basic`, `detailed` mode와 legacy missing-value=`hidden`, new-node=`detailed` 기본값을 검증한다.
- 팀별 온보딩 demo의 네 document-level KB는 정제 가능한 KB 이름을 `safe_metadata.safe_label`로 seed하며, direct KB Citation에서 일반 `참조 문서` 대신 해당 KB 이름을 표시한다.
- 최종 prompt char cap과 압축 이후 실제 포함된 evidence만 Citation이 되는지 검증한다.
- denied/revoked/source-deleted/archived 후보, no-evidence 결과와 control-only LLM node가 Citation을 만들지 않는지 검증한다.
- Collection evidence에서 child KB/document/chunk id, raw filename/path/URL, child section과 child-local rank가 제거되고 전역 evidence rank만 남는지 검증한다.
- source-managed 표시 정책이 unapproved/inactive이면 일반 라벨로 fail-closed하고 수동 `safe_label`로 우회하지 않는지 검증한다.
- `detailed` preview 길이 제한과 정제, 최대 item 수, stable dedupe를 검증한다.
- 사용자 sidecar가 durable WorkflowRun output과 일반 trace/audit에 저장되지 않는지 검증한다.

## MBA-305 Classification, Taxonomy And Processing Policy Tests

이 section은 [ADR-0065](../../decisions/ADR-0065-knowledge-classification-taxonomy-and-processing-profile.md)의
후속 구현 acceptance contract다. MBA-305 문서 PR 자체에 runtime test를 요구한다는 뜻은 아니다.
[보호 리소스 완결성 매트릭스](protected-resource-completion.md)는 각 경계를 `완료`, `해당 없음`,
`후속 이슈`로 구분한다. MBA-335, MBA-304, MBA-310, MBA-311과 MBA-309의 후속 PR은 자신이 소유한
동작 경계에 구현 위치와 실행 가능한 검증 증거를 추가해야 한다.

### Classification And Profile Contract Closure Trace

| Contract | Normal/absence boundary | Race/revocation boundary | Response/audit invariant |
| --- | --- | --- | --- |
| Assignment | Existing revision update와 `null -> revision 1` first mutation | Concurrent first PUT/accept, malformed `manual_axes`, selected lock, unselected-axis mismatch, taxonomy/registry/content change와 source revision/content access revoke | Complete set, exact changed-dimension/reason matrix, source-managed mutation의 lookup/commit source gate와 content-confirming에만 추가되는 `content_read`/raw policy, selected manual axis 전이와 unselected source/lock 보존, one revision+audit 또는 전체 rollback |
| Taxonomy authoring | Empty/complete forest, 1,000 topics/2,000 edges와 server-issued topic mapping round-trip | 1,001/2,001 bound, duplicate/raw-order, response loss, stale draft, key reuse/cross-draft ref | Full detail recovery, no truncation/client stable ID, fixed validation 또는 one draft revision+mapping |
| Taxonomy impact | `taxonomy_impact_bucket_v1`의 affected/reindex predicates와 0/1/10/11/100/101 boundaries | Assignment/content/lifecycle snapshot, bucket contract version, publish CAS/ack race | Exact count/identity 비노출, reindex subset, preview no side effect, publish+audit 또는 전체 rollback |
| Suggestion query/review | State-filtered bounded cursor page, selected-axis accept-preview/accept와 tagged generator provenance | Preview actor/scope/expiry/vector stale, source ACL revoke/expiry, content/taxonomy/assignment/resolver/current-decision/active-artifact race, generator kind/field mismatch | Hidden candidate 비노출, deterministic/AI provenance 분리, accepted axes만 authority 전이, same-boolean identity change도 stale zero-write, exact terminal retry와 terminal outcome/audit 원자성 |
| Profile override | Absent/current set·clear와 full resolver revision | Source gate revoke, content, assignment, registry, taxonomy, policy, Organization/Platform catalog, selectability, override 각각의 change | Protected lookup 전 source hiding, commit-time revoke zero-write 또는 override/audit/`reindex_required`가 한 snapshot |
| Profile schema/policy authoring | Exact `{}` first create/null-config shell, successor exact `base_revision_id`, V1 token lower/upper bound, strict scalar type, three matcher variants, complete draft와 0/1/2,000 rules | First-create wrong shape/audit failure, wrong-owner/identity/lifecycle base, capability 축소, 2,001 raw rules, duplicate matcher, unknown union field, incomplete Platform fallback, cross-organization/deprecated ref와 complete PATCH race | Exact first response 또는 base config/lineage, create audit 원자성, draft-only catalog revision 불변, coercion/partial merge/dedupe cap 우회 없음, fixed validation 또는 one draft revision; validate/preview/publish는 complete config/fallback만 허용 |
| Reindex admission | Canonical `Idempotency-Key` wire, fresh request와 current KB/source-authorized exact receipt replay, physical `job_created`의 exact-one embedding binding | Missing/duplicate/alias key, token expiry, KB/source revoke before lookup 또는 return/commit, credential 0/복수 및 binding issue/commit revoke, same-key same/different typed request concurrency | Invalid wire pre-DB `422`, raw key 비저장, scoped digest/public status/receipt 일치, authority/credential revoke zero-write/hiding, 최초 `knowledge.processing_reindex.admitted` audit 원자성, replay duplicate audit/decision/job/rebinding 없음 |
| Reindex worker/finalizer | `job_created` receipt에 불변 bind된 execution actor와 embedding binding의 fresh permission/source/credential gate | Cross-actor/credential job reuse, membership, KB/source authority 또는 credential lifecycle/relation/permission/provider-routing revoke와 external batch/finalize race | Actor/binding 교체 없음, revoke 뒤 새 adapter call·Satisfaction·pointer swap 없음, fixed cancelled+cleanup 또는 fence-lost no-commit |

### Axis And Pure Policy Tests

- `security_classification`, `document_type`, `taxonomy_topic_ids`, `chunking_profile_ref`가 별도
  input/result type으로 표현되고 topic/type/profile 변경이 security classification, KB permission,
  source ACL, retention 또는 legal status를 변경하지 않는지 검증한다.
- Document type source가 `manual`이고 topic source가 `rule`인 assignment와 그 반대 조합을 허용한다.
  Projection, resolver, canonical audit와 UI contract가 두 source/lock을 axis별로 보존하고 단일 source로
  올리거나 낮추지 않는다. Complete manual PUT도 selected `manual_axes`만 manual로 전환하고 unselected
  value/source/lock을 보존한다.
- Existing `meta_info.classification=public|internal|confidential|pii`는 보안 민감도로 유지한다.
  `public_law`, `internal_policy` 같은 legacy 비표준 값은 document type/topic으로 자동 변환하거나
  허용 보안 등급으로 확대하지 않는다.
- Document type resolver는 published platform registry의 stable ID만 허용한다. Unknown,
  deprecated, arbitrary label, Unicode confusable, case/whitespace 변형과 organization-defined 문자열을
  runtime capability로 받아들이지 않는다.
- Mixed document는 document-level `mixed` capability와 parser block `structure_kind`를 보존한다.
  Table/prose/appendix block이 있다는 이유로 hidden section/chunk semantic assignment를 만들거나
  document assignment를 여러 current type으로 분할하지 않는다.
- Taxonomy validate는 empty forest를 허용하되 cycle, self-parent, orphan, duplicate stable ID와
  cross-version/cross-organization parent를 fixed safe violation으로 거부한다.
- Complete taxonomy raw topic count `0, 1, 999, 1,000`과 replacement reference 합계 `0, 1, 1,999,
  2,000`은 schema/graph bound를 통과한다. Topic 1,001개 또는 edge 2,001개는 duplicate 제거와
  normalization/graph/repository 호출 전에 각각 `knowledge.taxonomy_topic_count_limit_exceeded`와
  `knowledge.taxonomy_replacement_edge_limit_exceeded`로 거부되고 draft revision, server ID mapping과 audit를
  만들지 않는다. 1,001개 raw entry 안의 duplicate 또는 2,001개 raw replacement 안의 duplicate도 먼저
  oversized로 거부해 dedupe로 bound를 우회하지 않는다. Raw UTF-8 label 512 byte는 허용하고 513 byte는
  normalizer 호출 전 `knowledge.taxonomy_label_invalid`다. Pinned normalization 뒤 comparison key도
  non-empty와 UTF-8 512 byte는 허용하고 513 byte부터 sibling uniqueness, graph와 repository 호출 전에 같은
  code로 거부한다. 특히 U+0130 `İ` 256개처럼 raw 512 byte지만 full case-fold 뒤 key가 512 byte를 넘는
  fixture가 post-normalization bound에서 실패해야 한다. 모든 failure는 draft revision, ID mapping과 audit가
  없고 valid 1,000-topic/2,000-edge forest는 누락·truncate·pagination 없이 round-trip한다.
- New topic에는 client stable ID를 허용하지 않고 valid UUID `draft_topic_key`만 받는다. Existing `topic_id`와
  new key의 parent/replacement discriminated reference, same-request child-to-new-parent와 split/merge ref가
  server-issued ID에 정확히 materialize되는지 검증한다. Missing/invalid key/ID, 둘 다 지정, duplicate definition
  key, draft 밖 key와 잘못된 reference shape는 `knowledge.taxonomy_draft_topic_reference_invalid`다. 같은 allocation
  request 안에서 parent/replacement가 new key를 반복 참조하면 같은 server ID로 resolve되어야 한다. Allocation
  성공 뒤 current-mapped key를 current revision의 새 topic definition 또는 relation reference로 다시 보내면
  `knowledge.taxonomy_draft_topic_key_conflict`이고 partial ID/topic/mapping/revision/audit가 없다. Subsequent
  mutation은 server ID를 사용한다. Complete PATCH로 topic을 제거하면 mapping도 같은 revision에서 사라지고
  current mapping 수는 topic 수 이하로 유지된다. 제거된 key를 다시 제출하면 새 stable ID가 발급되며 old ID를
  재사용하거나 old mapping을 되살리지 않는다.
- Successful create/PATCH response를 잃은 뒤 old `draft_revision`과 같은 key로 retry하면 unchanged 성공으로
  가장하지 않고 stale conflict다. Draft detail 재조회는 complete forest, current revision과 같은
  `draft_topic_key -> topic_id` mapping을 반환하고 이후 PATCH는 stable ID만 사용한다. Published/superseded
  detail, assignment option과 runtime payload에는 draft key가 나타나지 않는다.
- Unicode Character Database 14.0.0을 고정한 `unicode_14_0_nfkc_casefold_ws_v1` fixture에서 같은 parent의
  `Finance`, full-width `Ｆｉｎａｎｃｅ`,
  대소문자·앞뒤/연속 Unicode whitespace 변형은 같은 canonical key로 충돌한다. Root topic도 implicit
  parent 아래 sibling으로 충돌하고 같은 key가 서로 다른 parent 아래 있으면 유효하다. Whitespace-only label은
  `knowledge.taxonomy_label_invalid`, same-parent collision은 `knowledge.taxonomy_sibling_label_conflict`다.
  Client-supplied comparison key를 바꿔도 결과가 달라지지 않고 version detail은 exact normalizer contract
  version을 반환한다. Runtime Unicode library upgrade fixture도 published key를 바꾸지 않고 새 data version은
  새 normalizer contract 없이는 사용할 수 없다. Unresolved violation draft publish는
  `knowledge.taxonomy_publish_validation_failed`이고
  version/current pointer/identity ledger/replacement edge/audit를 만들지 않는다.
- Deprecated topic replacement는 같은 Organization의 candidate version에서 active인 target만 허용한다.
  Self/missing/cross-organization target과 existing ledger edge를 포함한 cycle은
  `knowledge.taxonomy_replacement_invalid`다. Longest directed path 8 edge는 유효하고 candidate edge가 9번째
  edge를 만들면 `knowledge.taxonomy_replacement_depth_exceeded`다. Many-to-one merge와 one-to-many split hint는
  validate할 수 있지만 publish 뒤 기존 assignment를 자동 변경하지 않고 `review_required`로 남긴다.
- Published taxonomy version은 PATCH할 수 없다. Draft publish는 하나의 current winner만 만들고
  stale `expected_current_taxonomy_version` loser는 mutation 없이 conflict가 된다.
- Current taxonomy가 없는 최초 publish는 validate/impact snapshot의
  `expected_current_taxonomy_version=null`로 null-to-candidate CAS에 성공한다. 이미 current가 있는데 null을
  보내거나 current가 없는데 non-null version을 보내면
  `knowledge.taxonomy_publish_snapshot_conflict`이고 version/current pointer/audit가 바뀌지 않는다. 두
  최초 publish가 같은 null snapshot으로 경합하면 winner 하나와 canonical audit 하나만 남는다.
- Current profile policy가 없는 최초 publish도 `expected_current_profile_policy_version=null`로 같은 absence CAS를
  사용한다. 두 null publish 경합의 loser는
  `knowledge.processing_profile_policy_publish_snapshot_conflict`이며 partial rule/audit를 남기지 않는다.
- Taxonomy와 type/general-only profile-policy가 서로의 null current snapshot을 본 상태에서 최초 publish로
  경합하면 shared Organization coordination lock에서 한 command만 commit되고 다른 command는 fixed snapshot
  conflict다. Loser는 winner snapshot으로 다시 validate/retry해야 하며 incompatible current pair, partial
  version/rule/audit를 남기지 않는다. Current taxonomy가 null인데 topic rule을 가진 policy candidate는 경합
  전 validation에서 거부된다.
- Topic이 0개인 draft는 cycle/orphan/schema error가 아닌 valid empty forest로 validate/publish된다. Current
  profile policy에 topic rule이 없을 때 empty version은 current가 되고 options의 topic 목록은 비지만
  document type/general profile path는 유지된다. Existing topic assignment는 삭제·자동 replacement되지 않고
  `review_required`가 된다.
- Current profile policy에 topic rule이 남은 상태의 empty taxonomy publish는 compatibility conflict다.
  Policy를 type/general-only version으로 먼저 publish한 뒤 같은 empty taxonomy가 성공한다.
- Taxonomy publish는 impact 0개, non-empty candidate와 empty forest 모두에서 same-snapshot impact preview를
  먼저 요구한다. `expected_impact_preview_revision` 누락, `impact_acknowledged` 누락/false, expired token,
  다른 actor/Organization/candidate/draft token은 `knowledge.taxonomy_impact_preview_stale`이고 version/current
  pointer/identity/replacement ledger/audit를 만들지 않는다.
- `taxonomy_impact_bucket_v1` table-driven fixture는 affected와 reindex-candidate exact internal count를 각각
  `0, 1, 10, 11, 100, 101`로 만들어 `none, small, small, medium, medium, large`를 반환한다.
  Response는 contract version을 포함하고 exact count/identity는 포함하지 않는다. Publish request의
  expected contract version 누락·mismatch 또는 token이 다른 contract version을 bind하면
  `knowledge.taxonomy_impact_preview_stale`이고 current pointer/ledger/audit가 없다.
- Affected predicate는 candidate 아래 topic-axis freshness가 `current`가 아닌 processing-eligible current
  assignment를 한 번만 센다. Candidate/current taxonomy projection의 freshness, resolver-eligible topic ID
  set/primary 중 하나 이상이 다른지 비교한다. Prior-version empty set,
  null-taxonomy empty set의 first publish와 active topic의 deprecate/remove처럼 tuple이 바뀌는 case는 포함한다.
  Exact version ID만 다르거나 이미 stale인 assignment가 같은 tuple을 유지하는 case, assignment가 없는 KB와
  archived/deleted/processing-ineligible target은 제외한다. `purging|purged` artifact가 있어도 assignment tuple이
  바뀌면 affected에는 포함한다. Same assignment의 여러 invalid topic이 count를 늘리거나 hidden
  identity/exact count를 노출하면 실패다.
- Reindex-candidate는 affected subset 중 active-ready artifact가 있는 row만 평가한다. Current decision의 비교 가능한
  `materialization_input_fingerprint`와 candidate fingerprint가 다른 row, 그리고 active-ready legacy artifact는 있지만
  current decision 또는 비교 가능한 fingerprint가 없는 row를 포함한다. 후자는 동일 materialization을 증명할 수
  없으므로 보수적 후보다. Artifact가 없거나 availability가 `purging|purged`인 affected row와 fingerprint가
  같음이 증명된 row는 reindex candidate에서 제외하고 모든
  fixture에서 reindex count가 affected count 이하인지 검증한다. 서로 다른 resolved scoped profile ref/source/status가
  같은 normalized materialization fingerprint를 만드는 fixture는 affected일 수 있어도 reindex candidate가 아니며
  후속 admission은 새 Decision Manifest와 `satisfied_existing`으로 수렴한다. Fingerprint가 실제로 다른 대조 fixture와
  legacy no-decision fixture만 reindex bucket에 포함한다. Preview/publish만으로 assignment, Decision Manifest,
  receipt 또는 reindex job이 생성되면 실패다.
- Impact preview 뒤 assignment current pointer, canonical content, explicit profile override, current Processing Decision
  pointer 또는 active artifact pointer/`ready|purging|purged` availability 중 하나가 바뀌어 server-owned assignment
  impact snapshot revision이 전진하면 old token publish는 stale conflict다. Bounded bucket이 우연히 같거나 Client가
  영향 없음이라고 주장해도 acknowledgement를 재사용하지 않는다. Current taxonomy/profile-policy/registry/
  Organization catalog/Platform catalog 중 하나만 바뀌는 table-driven case도 동일하게 전체 rollback한다.
- Assignment first create/replace/delete, same-value manual takeover, suggestion accept, current-validation, lock/unlock,
  새 canonical materialization input hash, impact scope를 바꾸는 KB/document lifecycle, override set/change/clear,
  current decision pointer와 active artifact pointer/availability 전이를 table-driven으로 실행해 authoritative mutation과
  `assignment_impact_snapshot_revision`이 같은 transaction에서 한 번만 전진하는지 검증한다. Finalizer의
  decision/artifact pointer swap도 포함하며 audit 실패, stale mutation 또는 fence loser는 pointer/availability와
  revision을 함께 rollback한다.
- Artifact purge는 pre-delete `purging` fence, durable intent와 첫 impact revision이 같은 transaction에서 commit되는지
  검증한다. 이 transaction 실패나 fence loser는 external delete 전에 모두 rollback한다. Physical deletion 뒤에는
  tombstone, `purged`, 다음 impact revision과 `knowledge.processing_artifact.purged` audit가 별도 completion
  transaction에서 함께 commit된다. Completion 실패는 pointer/availability를 `ready`로 rollback하지 않고
  non-retrievable `purging`을 유지하며 reconciler가 same generation으로 완성한다.
- Normalized unchanged no-op, suggestion create/reject/expire, read/preview, 같은 canonical input의 source generation,
  staging build/job/manifest 생성과 current pointer/availability를 바꾸지 않는 상태 갱신은 revision을 전진시키지 않는다.
- Successful taxonomy publish audit은 impact token 원문, hidden KB/document identity와 exact denied count 없이
  token digest, acknowledged flag, `taxonomy_impact_bucket_v1`, bounded impact bucket과 impact snapshot revision만 저장한다. Audit 저장 실패는
  publish/current pointer/ledger를 rollback한다.
- Taxonomy publish는 existing assignment, current decision/build manifest와 active ready pointer를 자동
  변경하지 않는다. Stable topic ID가 current version에도 유효한 explicit validation만
  last-validated taxonomy version을 전진시키며 assigned version과 과거 label/path snapshot은
  유지한다. Missing/deprecated ID는 review-required로 남는다.
- Explicit current-validation은 expected assignment, canonical content revision과 current registry/taxonomy
  version을 모두 검증한다. Valid stable IDs는 effective set과 axis별 source/lock/effective-from/최초 assigned
  snapshot을 보존한 새 revision으로 last-validated refs만 전진시키고 canonical audit 한 건을 같은
  transaction에 남긴다. 이미 current면 unchanged이고 missing/deprecated이면 `200 review_required`이며 두
  경우 모두 revision/audit/reindex를 만들지 않는다.
- Rename/move 뒤 stable topic ID와 과거 version snapshot이 보존된다. Deprecate/merge/split은 기존
  assignment를 삭제하거나 replacement로 자동 바꾸지 않는다.
- Published V1의 topic A를 V2에서 deprecate 또는 제거한 뒤 V3 candidate가 A를 같은 label, 다른 label,
  다른 parent 중 어느 형태로 active 복원해도 `knowledge.taxonomy_topic_id_reuse_conflict`다. 새 opaque ID로
  만든 topic은 허용되고 rejected publish는 version/current pointer/audit를 바꾸지 않으며 과거 A
  assignment/provenance는 review-required로 유지된다. Continuously active A의 rename/move는 tombstone
  재사용으로 오인하지 않는다.
- Taxonomy Topic Tombstone은 organization-lifetime identity ledger에 남아 version cleanup 뒤에도 재사용을
  차단한다. Source item `Tombstone` cleanup/sync handler가 이를 source deletion marker로 처리하거나 삭제하면
  실패다.
- First-published identity 또는 terminal topic tombstone append 실패와 canonical audit 실패는 taxonomy
  version/current pointer/ledger를 모두 rollback한다. Concurrent publish/reuse barrier에서 ledger와 current
  pointer가 서로 다른 winner를 가리키거나 tombstone 없는 removed ID를 남기면 실패다.
- Deprecated/missing topic assignment는 `review_required`로 projection되지만 hard filter, ranking
  boost와 profile mapping input에서 제외된다. 다른 valid topic까지 함께 제거하지 않는다.
- Topic assignment는 bounded unique set이다. Empty set, duplicate, primary-not-in-set, multiple primary,
  stale taxonomy version과 type/topic ID namespace 혼합을 각각 검증한다.
- Current taxonomy pointer가 없는 Organization은 null taxonomy version + empty topic set + null primary의
  type-only assignment를 생성·교체할 수 있다. 이 상태에서 non-null version, topic 또는 primary를 보내면
  mutation/audit/suggestion row 없이 거부한다. Required `taxonomy_version_id` 누락은 schema `422`, null과
  non-empty topic/primary 조합은 `422 knowledge.classification_taxonomy_shape_invalid`, same-scope current
  pointer mismatch는 `409 knowledge.classification_taxonomy_snapshot_conflict`다. Cross-organization/hidden
  version은 semantic mismatch보다 먼저 safe `404`다. Options response는 taxonomy version null과 empty topic
  options를 반환하고 fake root나 sentinel version을 만들지 않는다. Current taxonomy가 존재하면 topic set이
  비어 있어도 null version은 위 snapshot conflict이고 exact current empty-taxonomy version은 성공한다.
- Current taxonomy가 없는 상태의 classifier는 null taxonomy snapshot + empty topic set인 type-only
  suggestion만 만들 수 있다. 최초 taxonomy publish 뒤 아직 `suggested`인 candidate만 expired가 되고 새
  version으로 자동 승계하거나 topic candidate를 추가하지 않는다. Terminal outcome은 다시 쓰지 않는다.
- Null-taxonomy type-only suggestion accept와 최초 taxonomy publish를 barrier로 경합시키면 nullable current
  pointer와 candidate snapshot을 같은 accept transaction에서 다시 검증한 command만 commit된다. Publish
  winner 뒤 stale accept는 assignment revision, accepted outcome과 audit를 모두 남기지 않는다. Accept가 먼저
  commit되면 뒤의 publish는 accepted outcome과 safe provenance를 유지하고 assignment만
  current-validation-required로 projection한다.
- `PUT`은 complete effective set과 non-empty unique `manual_axes`를 요구하고 complete assignment revision을
  원자적으로 만든다. Omitted topic을 과거 set에서 유지하거나 suggestion candidate를 암묵 merge하지 않는다.
  Empty/duplicate/unknown axes와 first create에서 두 axis 미선택은
  `knowledge.classification_manual_axes_invalid`, unselected axis request value mismatch는
  `knowledge.classification_unselected_axis_conflict`이고 assignment/audit가 없다.
- Manual lock, manual assignment, human-accepted suggestion, deterministic rule, fallback precedence를
  모든 pairwise 조합으로 검증한다. Lower authority가 higher authority를 덮어쓰지 않는다.
- Locked와 unlocked manual assignment 각각에서 suggestion accept는
  `knowledge.classification_manual_authority_conflict`이고 assignment revision과 axis별 source/lock, suggestion
  state, audit와 reindex intent를 바꾸지 않는다. 같은 candidate set을 일반 assignment `PUT`으로
  제출할 때 candidate axis만 `manual_axes`로 선택하면 manual replacement로 성공하고 unselected axis source/lock은
  보존된다. Selected axis가 locked이면 같은 fixed conflict이고 unlock 전 partial revision/audit가 없다.
- Selected axis source가 deterministic rule, `accepted_suggestion`, fallback 등 non-manual인 unlocked assignment에
  같은 effective value와 해당 `manual_axes`를 일반 `PUT`으로 제출하면 value no-op이 아니라 selected-axis manual
  takeover 새 complete revision과 canonical audit가 생성된다. Selected source만 `manual`이 되고
  accepted-suggestion safe provenance는 과거 revision에서 유지되며,
  resolver state와 `reindex_required` projection을 다시 계산하되 durable job/active pointer는 자동 변경하지
  않는다. 이후 explicit admission의 materialization input이 같으면 `satisfied_existing`이고 physical build는
  생기지 않는다. Selected axes source가 이미 `manual`이고 complete value/unselected echoes가 current일 때만 같은
  요청이 `unchanged`다.
- Manual first create 또는 selected value 변경은 server-derived `manual_assignment`, selected value는 모두 같고
  before source가 `rule|accepted_suggestion|fallback`에서 `manual`로만 바뀌는 authority-only takeover는
  `manual_takeover`를 canonical audit에 기록한다. Value와 authority가 함께 바뀌면 `manual_assignment`가
  우선한다. Audit는 unselected axis의 unchanged source/lock과 before/after assignment revision을 검증한다. Raw label/content는
  없고 audit failure는 source/revision/projection을 함께 rollback한다.
- Manual PUT에 `reason_code`, free-text 또는 다른 unknown top-level field를 추가하는 table-driven request는
  DB 조회와 assignment/audit 전에 `422 knowledge.classification_request_invalid`다. Input을 response/log/audit에
  반사하거나 schema가 unknown field를 버린 뒤 성공하면 실패다.
- Locked type과 locked topic axis를 독립 검증한다. Type lock이 topic review를 불필요하게 막거나
  topic lock이 type을 자동 잠그지 않는다. Rule/accepted-suggestion source의 locked type과 unlocked topic 조합에서
  topic-only `manual_axes` PUT은 type value/source/lock을 보존하고 topic만 갱신한다. 반대 조합도 대칭적으로
  성공하며 complete revision/audit 하나만 만든다.
- Unlocked current axis의 lock은 exact non-null assignment revision과 current canonical content revision으로
  성공하고 complete assignment revision/audit 하나만 만든다. GET 뒤 source sync가 canonical content를 바꾸거나
  concurrent assignment replacement가 이기면 stale lock은 각각
  `knowledge.classification_lock_snapshot_conflict`로 lock/reindex projection/audit를 남기지 않는다. 같은 snapshot의
  concurrent lock은 winner 하나와 exact-state unchanged로 수렴하거나 conflict하되 중복 revision/audit가 없다.
- `current_validation_required` axis를 바로 lock하면 `knowledge.classification_lock_not_current`이고 explicit
  current-validation 뒤 fresh revision으로만 lock할 수 있다. Missing/deprecated ref의 `review_required` axis도 같은
  code로 거부되며 current option과 target `manual_axes`를 사용한 complete assignment replacement 뒤에만 lock할
  수 있다. Valid하거나 locked인 반대 axis의 source/lock은 replacement 전후 동일해야 한다. Lock request가
  stale 상태를 `review_recommended` resolver-eligible로 승격시키면 실패다. Locked review-required axis는
  manage/Organization manager unlock 뒤 complete replacement로만 복구되고 write-only actor가 lock을 우회하거나
  invalid reference를 자동 replacement하지 못한다.
- Unlock은 current canonical content precondition을 요구하지 않으며 stale content, missing/deprecated ref와
  `review_recommended|review_required` projection에서도 current assignment revision으로 성공한다. 다른 axis를
  보존하고 unlocked axis의 current freshness/resolver eligibility를 다시 계산하며 stale assignment revision은
  `knowledge.classification_assignment_revision_conflict`로 전체 rollback한다.
- 새 canonical content revision에서 locked type/unlocked topics와 unlocked type/locked topics를 각각 검증한다.
  Locked axis는 `review_recommended`, resolver-eligible이고 unlocked manual/rule/accepted-suggestion/fallback axis는
  `current_validation_required`, ineligible이다. Unlocked stale value는 provenance/display에는 남지만 hard filter,
  ranking, profile mapping과 materialized assignment field에 들어가지 않는다. Eligible axis rule이 없으면 general
  profile로 fallback하고 old active-ready artifact는 replacement ready 전까지 유지한다.
- Current-validation은 server가 expected snapshot과 current snapshot에서 `content|taxonomy|document_type_registry`
  changed-dimension set을 계산한다. Table-driven matrix는 변경 없음은 `unchanged`, 단일 content/taxonomy/registry는
  각각 `content_reviewed|taxonomy_updated|registry_updated`, 두 dimension의 모든 조합과 세 dimension 모두는
  `combined_review`만 성공함을 검증한다. Content가 바뀌었는데 content confirmation이 없는 reason은 기존
  `knowledge.classification_content_confirmation_required`, 그 밖의 과소·과다·잘못된 reason은
  `knowledge.classification_validation_reason_mismatch`이며 revision/audit/reindex projection을 만들지 않는다.
  성공은 effective value/source/lock/effective-from을 보존하고 manual-confirmation provenance와 last-validated refs만
  전진시킨다. Original rule/accepted-suggestion source를 manual로 바꾸지 않는다.
- `content_reviewed` 또는 content가 포함된 `combined_review`는 actor의 effective `content_read`와 applicable current display/raw
  policy를 요구한다. Source-managed KB는 fresh requester source authorization까지 검증하고 commit 직전
  authorization revision/watermark를 assignment snapshot과 함께 직렬화한다. Missing/revoked/stale gate와 gate 뒤
  revoke race는 Organization manager에게도 safe `404 resource.hidden`이며 revision/audit/reindex projection이 없다.
  Taxonomy/registry-only validation은 raw content나 source display authorization을 요구하지 않고 content 확인
  provenance를 만들지 않는다.
- Previous suggestion은 content revision/hash mismatch로 expired가 된다. Source generation만 바뀌거나 같은
  canonical input을 사용한 profile-only output DocumentVersion 교체는 assignment freshness나 suggestion을
  stale/expired로 만들지 않는다.
- Suggestion 상태 전이 `suggested -> accepted|rejected|expired`와 `abstained` terminal behavior를
  검증한다. Rejected, expired, abstained와 이미 accepted suggestion은 재-accept할 수 없다.
- Suggestion provenance는 `generator_kind` tagged union으로 검증한다. 두 kind 모두 immutable
  `generator_contract_ref`가 필요하다. `deterministic_rule`은 approved rule-set/version만 허용하고 model,
  prompt-template, calibration, confidence field나 fake provider ref가 있으면 거부한다. `ai_classifier`는 classifier
  policy/model/prompt-template/calibration ref와 bounded confidence를 모두 요구하며 rule-only field나 누락된 AI
  contract는 거부한다. Accept 뒤 assignment/audit safe snapshot도 같은 kind-specific field만 보존하고 operational
  suggestion purge 뒤 재현된다.
- Suggestion list item도 `generator_kind`와 opaque `generator_contract_ref`를 공통으로 반환한다. Deterministic
  item은 approved rule-set/version safe projection만, AI item은 calibrated state와 bounded confidence bucket만
  추가한다. Kind와 맞지 않는 provider field, raw score/prompt/rationale/content와 provider payload가 response에
  나타나면 실패다.
- Suggestion accept-preview는 accept와 같은 required nullable `expected_assignment_revision`과 non-empty unique
  `accepted_axes`를 사용한다. Fresh response는 최대 10분 `accept_preview_revision`, expiry,
  axis별 `unchanged|value_changed|authority_changed`, `profile_resolution_changed`, `reindex_required`와
  `active_ready_available`만 반환하고 `Cache-Control: no-store`를 사용한다. Value+authority 변경은
  `value_changed`, value가 같고 authority만 바뀌면 `authority_changed`, 둘 다 같거나 unselected axis이면
  `unchanged`인지 table-driven으로 검증한다. Candidate 밖/empty/duplicate axis는
  preview token 없이 기존 axes validation으로 거부하며 raw content, hidden identity, exact count/cost와 내부
  fingerprint가 response 또는 token plaintext에 나타나면 실패다.
- Preview 발급 뒤 suggestion candidate/state, accepted axes, assignment, canonical content, nullable taxonomy,
  registry, profile policy, Organization/Platform catalog, override, resolved scoped profile ref, materialization input,
  nullable current Processing Decision ref/fingerprints 또는 nullable active Artifact Build ref/generation/
  availability/materialization/integrity fingerprint를 하나씩 바꾸는 table-driven accept는 모두
  `409 knowledge.classification_suggestion_accept_preview_stale`이고 assignment/outcome/audit/reindex intent가
  0개다. Missing/expired/wrong actor·Organization·KB token도 같은 stale contract이며 token이 authority나
  candidate lookup capability가 되지 않는다. Source authorization revoke/expiry는 hypothetical projection과
  token lookup보다 먼저 safe `404 resource.hidden`으로 닫는다.
- Fresh authorization을 통과한 exact terminal accept retry는 stored non-reversible request fingerprint가
  일치하면 preview expiry 뒤에도 기존 safe result를 반환한다. Terminal lookup/fingerprint compare는 fresh
  parent/KB/source gate 뒤, preview expiry·stale 검사보다 먼저 수행한다. 다른 axes/revision/request는 terminal
  outcome을 변경하지 않고 duplicate assignment/audit/reindex intent를 만들지 않으며 raw preview token/digest는
  durable row, log, audit와 trace에 저장하지 않는다. Commit 뒤 response loss와 preview 만료 후 exact retry를
  포함한 table-driven test가 새 preview 없이 같은 result로 수렴해야 한다.
- Suggestion candidate가 current taxonomy allowlist 밖이거나 deprecated면 effective assignment를
  만들지 않는다. Classifier가 임의 label을 반환해도 ID로 자동 생성하지 않는다.
- Uncalibrated raw score는 correctness probability, auto-accept threshold, security decision과 UI
  accuracy percentage로 사용되지 않는다. Calibration revision이 없는 candidate는 calibrated
  state로 projection되지 않는다.
- Current profile-policy가 있는 resolver는 explicit override, type+primary, type, primary, organization general,
  policy platform general precedence를 table-driven test로 검증한다. 동일 specificity 충돌은 policy publish
  validation에서 거부하고 catalog Platform default를 policy fallback으로 혼합하지 않는다.
- Brand-new Organization에서 current profile-policy와 explicit override가 모두 없으면 exact
  `platform_default_profile_revision`을 resolve하고 source가 `platform_default`인지 검증한다. Current policy를
  최초 publish하면 resolver가 policy branch로 전환되고 이전 null-policy preview/override/reindex token은 stale
  conflict로 zero-write 종료한다.
- Platform registry owner/system actor의 default pointer command는 required exact expected
  `expected_platform_profile_catalog_revision`과 `target_profile_revision_id`를 받는다. Success는 target selectability를 다시 확인하고
  pointer, catalog revision 한 번 증가와 `knowledge.processing_profile_default.changed` audit 한 건을 같은
  transaction에서 확정한다. Audit에는 safe actor, before/after Platform profile revision과 resulting catalog
  revision만 있고 raw profile config는 없다. Stale expected revision 또는 audit 저장 실패는 pointer/catalog/audit
  전체를 rollback한다. 변경 전 default ref/catalog revision을 bind한 preview, override와 reindex admission은 모두
  stale이며 각 stale request는 receipt, override, 추가 audit와 job을 만들지 않는다. Same-revision default mutation/deprecate race는 serialization
  winner 하나만 만들고 current default target deprecate는 pointer를 다른 selectable revision으로 옮기기 전
  `knowledge.processing_profile_in_use`로 거부된다.
- Null-policy이고 valid explicit override가 없어 default branch가 필요한데 target이
  missing/deprecated/incompatible이면 override GET/options는 `200`,
  `effective_resolution_status=platform_default_unavailable`, null `resolved_profile_revision_ref`와 current vector를 bind한 opaque
  resolver revision을 반환한다. Preview, reindex admission과 override clear는
  `503 knowledge.processing_platform_default_unavailable`이고 token/receipt/override/audit/job이 0개다. Selectable
  explicit override set/change는 GET의 resolver revision, full current snapshot과 target을 다시 검증해 canonical
  audit 한 건과 함께 recovery에 성공하고 이후 preview source는 `override`다. 기존 active-ready artifact는
  유지하며 mutable latest, environment default 또는 legacy character setting을 fallback으로 사용하지 않는다.
- Organization general rule이 없는 published policy도 approved policy Platform general fallback이 있으면
  publish/resolve할 수 있다. Platform general이 없거나 missing/deprecated/incompatible이면 policy publish는 fixed
  validation error로 거부되고 partial current pointer/audit가 없다. Unknown/unclassified, deprecated-only topic
  set, missing org general과 stale mapping은 policy Platform general까지 deterministic하게 fallback한다.
  Missing/deprecated/incompatible explicit manual override는 조용히 general로 낮추지 않고 review-required
  conflict로 preview/reindex를 차단하며 기존 active ready version을 유지한다. Mutable latest lookup이나 random
  ordering을 사용하지 않는다.
- Organization manager는 stable profile identity+first draft 생성, successor draft, list/detail, PATCH/validate,
  publish와 deprecate를 수행할 수 있다. Non-manager는 `403`, wrong organization identity/revision은 semantic
  validation보다 먼저 safe `404`이고 platform option은 Organization API에서 read-only다.
- First profile create는 exact `{}` request만 받는다. Success `201`은 profile ID, unchanged current Organization
  catalog revision과 `revision_state=draft`, null base/config, `draft_revision=0`인 exact nested draft projection을
  반환하고 identity, draft와 `knowledge.processing_profile_draft.created` audit 한 건을 원자 생성한다. Missing
  body, null/array/scalar와 각 unknown field 하나를 넣은 object는 profile repository 조회 전
  `422 knowledge.processing_profile_invalid`이며 identity/draft/catalog/audit가 0개다. Audit commit failure도
  identity/draft를 rollback한다. Recovered shell은 list/detail에 보이지만 complete config PATCH 전 validate/publish는
  실패하고, complete first PATCH만 revision을 1로 전진시킨다.
- Successor request는 exact `base_revision_id` 하나만 받는다. 같은 profile identity에 config가 다른 published
  revision A/B를 만든 뒤 B를 base로 요청하면 list/current ordering과 무관하게 B의 normalized config만 복제하고
  response와 새 draft lineage에 B ID, `draft_revision=0`을 기록한다. Missing/malformed/extra field는 profile/base
  repository 조회 전 `422 knowledge.processing_profile_invalid`이고 first-draft create에는 base field가 없다.
- Cross-organization base와 path의 다른 profile identity에 속한 base는 ownership-first safe `404`다. Same-scope
  mutable draft, deprecated 또는 그 밖의 non-published base는
  `409 knowledge.processing_profile_successor_base_invalid`이며 새 draft, catalog revision과 canonical audit가 0개다.
- Profile draft `draft_revision` 0/1 및 concurrent same-revision PATCH를 검증한다. No-op은 unchanged이고 stale
  loser는 `knowledge.processing_profile_draft_revision_conflict`다. Publish/deprecate same catalog revision race는
  한 winner만 만들고 loser는 `knowledge.processing_profile_catalog_revision_conflict`이며 catalog/audit partial
  write가 없다.
- Profile validation은 `processing_profile_schema_v1` strict token bound, unknown tokenizer/parser/representation/
  embedding/model ref, character unit, credential/endpoint/secret/arbitrary executable option을 검증한다. Invalid
  input은 `knowledge.processing_profile_invalid`로 raw config/provider detail 없이 닫고 published revision을 만들지
  않는다.
- Size boundary는 `chunk_size_tokens=63` 실패, `64` 성공, `8192` 성공(선택 capability가 허용할 때),
  `8193` 실패를 검증한다. Overlap은 같은 size에서 `-1` 실패, `0` 성공,
  `floor(chunk_size_tokens / 2)` 성공, 그보다 1 큰 값 실패를 검증하고 inclusive 경계를 임의로
  `size-1`까지 넓히지 않는다.
- Size/overlap의 boolean, numeric string, float와 null은 정수로 coercion하지 않고 실패한다. Current published
  tokenizer/parser/representation/embedding capability가 V1보다 작은 한계를 가지면 그 한계를 적용하고, 더 큰
  capability fixture도 size 8,193 또는 half-size 초과 overlap을 허용하지 않는다. Bound 변경은 같은
  `processing_profile_schema_v1` revision을 재해석하지 않고 새 schema version을 요구한다.
- `processing_profile_policy_v1` policy document는 required `policy_schema_version`, raw `rules`, required nullable
  `organization_general_profile`과 `platform_general_profile`만 허용한다. Create wrapper는 `policy`만 받아
  `draft_revision=0`을 만들고 PATCH wrapper는 `expected_draft_revision`과 `policy`만 허용한다. Missing/unknown
  wrapper field, create의 expected revision, PATCH의 omitted expected revision과 server default/merge는
  `knowledge.processing_profile_policy_invalid`다. Raw rules 0, 1, 2,000개는 shape/cap을
  통과하고 2,001개는 duplicate normalization 전에
  `knowledge.processing_profile_policy_rule_limit_exceeded`로 draft/audit 없이 거부된다. 2,001개의 동일 rule을
  dedupe해 cap을 우회할 수 없다.
- Matcher union은 `type_primary`에서 document type+primary topic만, `type`에서 document type만, `primary`에서
  primary topic만 요구한다. 각 variant의 missing/forbidden/unknown field와 잘못된 discriminant는
  `knowledge.processing_profile_policy_invalid`다. Target은 exact
  `{catalog_scope: organization|platform, profile_revision_id}`이며 Organization general에는 organization,
  Platform general에는 platform scope만 허용한다.
- 두 general field는 생략할 수 없고 null은 incomplete draft 저장에서만 허용한다. Validate/impact-preview/publish는
  non-null Platform general을 요구하고 cross-organization ref는 ownership-first safe `404`, same-scope
  deprecated/incompatible ref는 fixed safe validation이다. Complete PATCH는 omitted rule을 실제로 제거하며
  JSON Patch, per-rule partial update 또는 omitted-field merge를 허용하지 않는다. 같은 normalized matcher의
  duplicate는 `knowledge.processing_profile_policy_rule_conflict`이고 draft/current/audit partial write가 없다.
- Published profile config는 PATCH/hard-delete할 수 없다. Current mapping policy 또는 current override direct
  reference가 있으면 deprecate는 `knowledge.processing_profile_in_use`이고 referring KB identity/exact count를
  노출하지 않는다. Successor policy publish/override clear 뒤 deprecate는 lifecycle event, incremented catalog
  revision과 canonical audit를 원자 확정한다. Historical policy/manifest/satisfaction과 active-ready artifact는
  보존하지만 deprecated revision은 신규 option과 `satisfied_existing`에서 제외되고 reindex는 자동 시작하지 않는다.
- Profile-policy validate/impact preview는 Organization과 Platform catalog revision을 각각 반환한다. Publish에서
  `expected_organization_profile_catalog_revision` 또는 `expected_platform_profile_catalog_revision`을 누락하면
  schema `422`이다. Preview-bound expected field/token claim mismatch 또는 preview 뒤 어느 catalog라도 바뀌면
  `knowledge.processing_profile_policy_impact_preview_stale`로 policy/current pointer/audit 전체가 rollback한다.
  Fresh token과 전체 snapshot 검증 뒤 coordination/current policy pointer CAS loser만
  `knowledge.processing_profile_policy_publish_snapshot_conflict`를 사용한다.
- Profile-policy impact-preview request는 `expected_draft_revision` 하나만 허용하고 fresh response는 exact
  draft/current policy/taxonomy/registry, Organization/Platform catalog,
  `assignment_impact_snapshot_revision`,
  `impact_bucket_contract_version=profile_policy_impact_bucket_v1`, 두 bounded bucket, 최대 10분 opaque token과
  expiry를 반환한다. Unknown request/response field, exact count/identity, raw token digest와 missing `no-store`는
  실패다.
- `profile_policy_impact_bucket_v1` table-driven fixture는 affected-resolution과 reindex-candidate internal count를
  각각 `0, 1, 10, 11, 100, 101`로 만들어 `none, small, small, medium, medium, large`를 반환한다. 두 count는
  독립 bucket이고 모든 fixture에서 reindex candidate가 affected resolution 이하여야 한다.
- Affected predicate는 current와 candidate resolver를 같은 snapshot에서 실행해 status/source/profile revision이
  달라지는 processing-eligible active target을 하나씩 센다. Current policy가 null인 Platform default target,
  assignment가 없는 Organization/Platform general target과 current assignment가 있는 matcher target을 포함한다.
  Candidate와 같은 profile/source/status로 resolve되는 target, valid explicit override가 계속 우선하는 target,
  invalid override가 계속 review-required인 target과 archived/deleted/processing-ineligible KB/document는 제외한다.
  Resolver 차이가 있는 active·processing-eligible target의 artifact availability를 `ready -> purging -> purged`로
  전이하면 affected count는 유지되고 reindex candidate에서만 제거되며 각 availability mutation은 old preview를
  stale로 만든다.
- Active artifact가 없는 affected target은 affected bucket에만 포함한다. Active-ready artifact/current decision의
  비교 가능한 fingerprint가 있으면서 candidate materialization fingerprint가 다르면 reindex candidate에 포함한다.
  Active-ready legacy artifact는 있지만 current decision 또는 비교 가능한 fingerprint가 없는 affected target도
  보수적으로 reindex candidate에 포함한다. Profile revision이 달라도 materialization fingerprint가 같음이 증명된
  resolver-only target은 reindex candidate에서 제외한다. Preview와 publish가 assignment, Decision Manifest,
  receipt 또는 reindex job을 만들면 실패다.
- Profile-policy publish는 fresh response의 compatibility snapshot,
  `expected_impact_bucket_contract_version=profile_policy_impact_bucket_v1`, opaque preview token과
  `impact_acknowledged=true`를 모두 요구한다. Server-owned `assignment_impact_snapshot_revision`은 token claim으로
  검증하고 별도 expected request field를 보내면 strict schema `422`다. Token 누락/expiry, false acknowledgement, wrong actor/Organization/
  candidate/draft, current policy/taxonomy/registry, 어느 catalog 또는 assignment-impact snapshot 변경과 contract
  mismatch는 `knowledge.processing_profile_policy_impact_preview_stale`이고 policy/current pointer/audit가 0개다.
  Valid token 확인 뒤 commit CAS loser만 `knowledge.processing_profile_policy_publish_snapshot_conflict`를 사용한다.
- Impact `none`도 preview/acknowledgement를 생략하지 않는다. 같은 token concurrent publish는 current pointer와
  `knowledge.processing_profile_policy.published` audit winner 하나만 만들고 loser는 partial version/audit 없이
  conflict한다. Success audit은 raw token 대신 safe digest, acknowledged flag, contract version, bounded buckets와
  impact snapshot revision만 포함하고 raw policy/profile config, exact count와 hidden identity가 없다.
- Organization profile deprecate와 Platform profile deprecate가 각각 current-reference 부재를 읽은 뒤 같은
  revision을 참조하는 policy publish 또는 override set과 경합하는 두 commit order를 table-driven으로 검증한다.
  Policy publish는 applicable Organization catalog, Platform catalog, coordination, taxonomy current, policy current의
  deterministic lock order를 사용한다. Catalog publish/deprecate, taxonomy publish와 profile-policy publish를
  동시에 시작해 timeout/deadlock 없이 한 serialization winner 또는 bounded stale conflict로 수렴하는지도 검증한다.
  Deprecate가 먼저
  commit되면 policy publish는
  `knowledge.processing_profile_policy_impact_preview_stale`, override set은
  `knowledge.processing_profile_override_snapshot_conflict`이고, 신규 current reference가 먼저 commit되면 deprecate는
  `knowledge.processing_profile_in_use`다. Applicable shared catalog serialization/CAS winner 하나만 commit하며 어떤 순서에서도
  deprecated revision을 가리키는 current policy/override, partial lifecycle event, catalog revision 또는 audit가 남지 않는다.
- Explicit override set/change/clear는 current selectable immutable profile만 사용한다. Set/change는
  resolver source를 override로 바꾸고 clear는 mapping precedence를 다시 적용하지만 어느 mutation도
  reindex를 자동 시작하거나 active pointer를 바꾸지 않는다. Missing/deprecated/incompatible current
  override `GET`은 generic state와 clear용 override revision을 반환하고 hidden profile identity를
  노출하지 않는다. 해당 revision으로 clear할 수 있으며 target profile lookup 실패 때문에 복구가
  막히지 않는다.
- Existing character-based chunk size/overlap은 legacy adapter input으로 유지한다. 같은 숫자가 token
  unit으로 재해석되거나 tokenizer/version 없는 manifest가 ready가 되면 실패다.
- Query intent와 document type을 독립 input으로 검증한다. 같은 document type에 fact/global query가
  서로 다른 strategy candidate를 가질 수 있고 stored type이 request strategy를 강제하지 않는다.

### Permission And Tenant Tests

- Classification read는 KB `read`, unlocked mutation/current-validation/suggestion review/profile
  preview/reindex는 KB `write`, lock/unlock/override options/read/set/clear는 KB `manage`, taxonomy/profile
  policy current/history read/author/publish는 Organization manager를 요구한다. 같은 active organization의
  Organization manager는 ADR-0034에 따라 각 KB-scoped read/write/manage 요구를 별도 KB grant 없이 충족한다.
- Source-managed KB의 모든 current-validation은 fresh source/display gate를 요구한다. Content가 changed
  dimension이면 위 KB `write` 외에 effective `content_read`와 applicable raw policy를 추가로 요구한다.
  Organization manager override는 KB action을 충족할 수 있지만 source-managed gate를 우회하지 않는다.
  Taxonomy/registry-only validation은 content-plane 권한을 불필요하게 요구하지 않지만 revoked source에서
  validation을 허용하지 않는다.
- Source-managed KB의 classification GET/options, assignment PUT, current-validation, lock/unlock,
  override GET/options/set/clear와 resolve-preview는 각각 충분한 KB action 또는 Organization manager override가
  있어도 assignment/lock/override/resolver repository lookup·projection·mutation 전에 fresh requester source
  authorization과 display policy를 평가한다. Fresh gate는 bounded safe projection을 반환하고
  missing/inactive/stale/unmapped/ambiguous/unverified/revoked/denied/unknown/expired 또는 scope mismatch는 모두 safe
  `404 resource.hidden`이다. Failure response/cache에는 assignment/override 존재, document type/topic/security
  classification, source/lock, stable profile ID, resolver status/revision, option count와 estimate가 없고 protected
  repository query가 호출되지 않는다. 같은 table-driven fixture의 manual KB에는 source gate를 적용하지 않는다.
- `GET /knowledge/document-types`는 authenticated active organization member에게만 bounded safe
  registry projection을 반환한다. Full organization taxonomy/profile-policy current/history는
  Organization manager 전용이고, KB `write` 또는 Organization manager actor는 KB-scoped
  `classification/options`만 읽는다.
  KB `read` actor는 effective classification response에 포함된 assigned stable ID와 bounded label만
  볼 수 있고 options 또는 full organization taxonomy를 조회하지 못한다.
- Document-type registry와 classification options endpoint는 active organization membership과 current
  platform registry visibility를 검증한다. Platform document type ID를 cross-organization resource로
  분류하지 않으며 unknown/hidden registry reference는 fixed unavailable contract로 처리한다. Taxonomy,
  profile-policy와 organization-owned profile endpoint는 active organization mismatch와
  cross-organization ID에서 safe hiding을 적용한다. Response에는 exact safe version, selectable stable
  ID와 bounded approved label/path 외 authoring metadata, hidden/deprecated option, impact count와 다른 KB
  assignment를 포함하지 않는다.
- `catalog_manage`, `permission_delegate`, `lifecycle_manage`, `sync_manage`, Collection
  `read/route/manage/sync`, organization membership만으로 위 권한이 생기지 않는다.
- Team/user direct additive KB grant의 effective permission을 사용하되 owner attribution만으로
  classification capability를 부여하지 않는다.
- 별도 KB grant가 없는 같은 active organization의 Organization manager는 ADR-0034 override로 KB-scoped
  classification read/write/manage operation을 수행할 수 있다. 동일 actor의 inactive/cross-organization context는
  허용하지 않고, 일반 member와 위임된 Knowledge domain action만 가진 actor는 필요한 KB grant 없이 수행할 수
  없다. Organization manager도 source-managed classification/override/resolve/suggestion의 fresh source/display
  gate는 우회하지 못한다.
- Missing, archived/deleted/hidden KB와 cross-organization taxonomy/policy version,
  assignment/suggestion, organization-owned profile/override와 reindex ID는 parent ownership을 lifecycle,
  terminal/deprecated/compatibility와 revision 검사보다 먼저 확인해 safe hiding을 적용한다. 존재 여부,
  label, candidate 수와 assignment revision을 노출하지 않는다.
- Same-scope insufficient permission은 approved `403` contract를 사용하고 denied mutation이
  assignment, lock, suggestion outcome, reindex job 또는 active pointer를 바꾸지 않는다.
- Reindex receipt의 actor ref, job ID, preview token과 owner/fencing token은 capability가 아니다. 최초
  `job_created` receipt actor만 job execution actor로 bind되고 다른 actor의 `job_reused` receipt가 이를
  교체하지 않는다. Worker claim, 각 source/provider external batch와 finalizer는 immutable job execution actor의 current active membership,
  effective KB `write` 또는 Organization manager authority, applicable source authorization 및 immutable embedding
  binding의 credential lifecycle, exact model relation, `use`와 revisions를 다시 평가한다.
  Authorization session은 외부 I/O 전에 종료하고 current unauthorized requester에게 job/receipt identity를
  노출하지 않는다.
- Active organization header와 path organization이 다르면 taxonomy query/mutation을 수행하지
  않는다. Client-supplied organization ID를 assignment authority로 사용하지 않는다.
- Source-managed KB classifier input은 fresh requester source authorization, display/raw policy,
  redaction과 provider egress를 모두 통과해야 한다. Organization manager, KB manage 또는 manual
  classification 권한이 source gate를 우회하지 않는다.
- Source authorization revoke/expiry가 classification preview와 suggestion 생성 사이에 발생하면
  provider call 또는 durable suggestion 저장 전에 current gate를 다시 확인하고 fail-closed한다.
- Source-managed assignment PUT/current-validation/lock/unlock/override set/clear가 initial source gate를 통과한 뒤
  commit 전에 source authorization revision/watermark가 바뀌는 barrier test에서 revoke transaction이 winner이면
  stale command는 `404 resource.hidden`으로 assignment/lock/override, impact snapshot revision, canonical audit와
  `reindex_required` projection을 하나도 남기지 않는다. External authorization session/DB transaction을 겹쳐
  유지하거나 stale allow 결과를 commit capability로 사용하면 실패다.
- Source-managed KB의 suggestion first/next page, accept-preview, accept와 reject를 각각 호출할 때 fresh source authorization과
  display policy를 candidate row query/projection 전에 다시 평가한다. KB `write/manage` 또는 Organization
  manager여도 revoked/stale/denied source gate를 우회하지 못하고 모두 동일한 `404 resource.hidden`이며
  suggestion ID/state/count/confidence, cursor continuation과 terminal outcome을 반환하거나 변경하지 않는다.
- Suggestion page/accept-preview/accept/reject가 source authorization을 통과한 뒤 commit/projection 전에 revocation watermark가
  바뀌는 barrier test에서 stale request는 candidate, assignment, outcome과 audit를 남기지 않는다. Cursor와
  suggestion ID를 다른 actor 또는 권한 회수 뒤 재사용해도 capability가 되지 않는다.

### API, Concurrency And Transaction Tests

- Malformed UUID, oversized topic set, duplicate ID, invalid primary, unknown reason code와 schema type은
  DB/provider 호출 전에 `422`다. Hidden target을 schema detail로 역추론할 수 없게 한다.
- Current taxonomy의 valid stable ID로 만든 assignment topic array 0, 1, 31, 32개는 schema bound를 통과하고
  33개 unique 또는 duplicate가 섞인 33개 배열은 dedupe 전에
  `knowledge.classification_topic_limit_exceeded`로 거부된다. 32개 이하 duplicate 배열은 별도 duplicate
  validation으로 거부하고 server가 조용히 제거하지 않는다. Rule/AI suggestion candidate와 selected-axis accept
  결과도 32개는 허용한다. Classifier output 33개 fixture는 provider result 이후 즉시 같은 fixed limit error로
  닫고 durable suggestion/assignment/audit를 만들지 않는다. 순서만 다른 유효한 32개 set은 같은 canonical
  fingerprint를 만든다.
- Assignment가 없는 GET은 nullable revision을 반환하고 fake revision/sentinel을 만들지 않는다. Required
  `expected_assignment_revision=null`인 최초 PUT 또는 최초 suggestion accept만 revision 1을 만들 수 있고,
  absent+non-null과 existing+null은 `knowledge.classification_assignment_revision_conflict`다. 최초 PUT은
  `manual_axes=[document_type,topics]`만 허용하고 한 axis만 선택한 first create는
  `knowledge.classification_manual_axes_invalid`로 row/revision/audit를 만들지 않는다.
- 같은 assignment 부재를 관찰한 PUT/PUT과 PUT/suggestion-accept barrier race는 각각 winner 하나만 revision 1,
  current pointer와 canonical audit를 commit한다. Loser는 value가 같아도 fixed conflict이며 orphan revision,
  terminal suggestion outcome, partial axis source/lock 또는 duplicate audit를 남기지 않는다.
- Two concurrent assignment updates with the same expected revision produce one winner and one `409`.
  Loser는 topic 일부, lock, audit 또는 reindex intent를 commit하지 않는다.
- Locked non-manual type을 보존하는 topic-only `manual_axes` PUT과 type unlock/update를 같은 expected revision으로
  barrier 경합시킨다. 한 complete revision만 winner가 되고 loser는 assignment revision conflict다. Topic-only
  winner가 type source/lock을 request echo나 manual로 덮어쓰거나 unlock winner 뒤 stale topic PUT이 old lock을
  복원하면 실패다.
- Lock과 assignment update, unlock과 suggestion accept, taxonomy publish와 assignment accept가 경합할
  때 row lock/CAS winner의 current state만 commit된다. Last-write-wins silent overwrite가 없다.
- Lock이 current assignment/content를 읽은 뒤 source sync 또는 current-validation/assignment replacement가
  commit하는 양쪽 순서의 barrier test는 lock의 assignment/content snapshot을 같은 transaction에서 재검증한다.
  Stale loser는 `knowledge.classification_lock_snapshot_conflict`이고 stale unlocked axis를 resolver-eligible로
  만들거나 partial lock/audit를 남기지 않는다. Unlock은 content race와 무관하게 current assignment winner에만
  적용되어 authority를 낮추는 recovery를 보장한다. Unlock transaction은 Client content precondition 없이 current
  canonical content pointer를 직렬화해 commit 시점 projection을 계산한다.
- Unlocked manual assignment와 suggestion accept가 경합하면 accept는 lock뿐 아니라 winner의 current
  authority를 다시 읽는다. Manual winner 뒤 accept가 candidate set을 적용하거나 suggestion을 terminal로
  바꾸면 실패다.
- Suggestion 조회 뒤 content update가 commit되면 stale accept는 current content/version mismatch로
  conflict가 되고 새 DocumentVersion에 assignment를 적용하지 않는다.
- Taxonomy current version 전환 뒤 old-version suggestion accept와 assignment PUT은 conflict다.
  Rename/move처럼 stable ID가 남아도 request의 expected version 검증을 생략하지 않는다.
- Assignment mutation과 canonical audit는 같은 transaction이다. Audit flush/commit 실패는 assignment,
  lock, suggestion outcome과 reindex intent를 모두 rollback한다.
- Successful current-validation의 canonical audit flush/commit이 실패하면 새 assignment revision과
  last-validated refs를 모두 rollback한다.
- Successful current-validation은 current resolver state와 `reindex_required` projection을 다시 계산하지만
  durable reindex job, Processing Decision Manifest 또는 active pointer를 자동 생성·변경하지 않는다.
  Materialization input이 달라진 경우에도 명시적인 preview/reindex admission 전까지 기존 active ready
  artifact를 유지한다.
- Operation-to-action table test는 ADR-0008과 동일한 22개 exact string을 `AuditAction`, audit 검색/UI filter와
  service mapping에서 검증한다. Taxonomy draft create/update/publish는
  `knowledge.taxonomy_draft.created`, `knowledge.taxonomy_draft.updated`, `knowledge.taxonomy.published`다. Manual
  assignment first create/update/lock/unlock은 `knowledge.classification_assignment.created`,
  `knowledge.classification_assignment.updated`, `knowledge.classification_assignment.locked`,
  `knowledge.classification_assignment.unlocked`다. Current-validation과 suggestion accept/reject는
  `knowledge.classification.revalidated`, `knowledge.classification_suggestion.accepted`,
  `knowledge.classification_suggestion.rejected`다. Processing profile draft create/update/publish/deprecate는
  `knowledge.processing_profile_draft.created`, `knowledge.processing_profile_draft.updated`,
  `knowledge.processing_profile.published`, `knowledge.processing_profile.deprecated`다. Profile-policy draft
  create/update/publish는 `knowledge.processing_profile_policy_draft.created`,
  `knowledge.processing_profile_policy_draft.updated`, `knowledge.processing_profile_policy.published`다. Platform
  default, override set/clear와 reindex admission은 `knowledge.processing_profile_default.changed`,
  `knowledge.processing_profile_override.set`, `knowledge.processing_profile_override.clear`,
  `knowledge.processing_reindex.admitted`다. Physical artifact purge 완료는
  `knowledge.processing_artifact.purged`다. Alias, combined wildcard string과 operation별 임의 action이 있으면
  실패다.
- 각 action fixture는 authoritative mutation과 audit flush/commit을 한 Unit of Work에서 실행한다. Normalized
  draft/manual no-op, unchanged lock, exact terminal accept/reject retry와 exact reindex receipt replay는 새 action을
  만들지 않는다. Audit 실패는 해당 draft/current pointer/assignment/lock/outcome/profile lifecycle/override/receipt를
  전부 rollback하고 action만 남거나 mutation만 남으면 실패다. Artifact purge completion audit 실패는 이미
  완료된 physical deletion을 rollback 대상으로 삼지 않고 tombstone/`purged`/impact revision 없이
  non-retrievable `purging`을 유지한다. Same-generation reconciler retry는 completion DB state와
  `knowledge.processing_artifact.purged` action을 정확히 한 번만 만든다.
- Current expected revision, normalized complete set과 selected `manual_axes`의 server-derived source가 모두
  동일한 manual assignment replacement만 `unchanged`를 반환하고 revision/audit/reindex를 증가시키지 않는다.
  Selected non-manual source의 같은 value는 selected-axis manual takeover로 commit하고 unselected authority는
  보존한다. Expected revision이 stale하면 desired set과 target source가
  같아도 `409`이며 endpoint와 service가 서로 다른 결과를 만들지 않는다. Manual takeover audit 실패는
  source/revision 전이를 rollback한다.
- Taxonomy/profile-policy draft PATCH는 `draft_revision` CAS를 사용한다. 같은 revision의 concurrent
  PATCH는 한 winner만 만들고 loser는 `409`이며, stale payload가 current와 같아도 성공으로 바꾸지
  않는다. Current normalized no-op은 unchanged이고 revision/audit를 만들지 않는다.
- Draft validate/impact-preview는 검사한 exact draft revision을 반환한다. 그 뒤 PATCH가 commit되면
  stale validation/preview revision으로 publish할 수 없고, publish는 draft revision과 current
  published pointer CAS 중 하나라도 stale이면 version/current pointer/audit를 모두 유지한다.
- Impact preview token은 exact actor/Organization/candidate/draft/current taxonomy/profile-policy/registry,
  Organization/Platform catalog, assignment-impact snapshot과 expiry에 bind된다. Token 발급과 publish 사이 각
  input을 하나씩 변경하는
  table-driven barrier에서 stale publish는 `knowledge.taxonomy_impact_preview_stale`이고 acknowledgement token을
  capability나 idempotency key로 취급하지 않는다. 같은 token의 concurrent publish는 current pointer CAS winner
  하나와 canonical audit 하나만 만든다.
- 최초 publish preview의 nullable current pointer는 sentinel이나 임의 version으로 바꾸지 않는다. Null
  snapshot을 받은 뒤 다른 manager가 최초 publish하면 stale null request는 fixed snapshot conflict이고
  새 current를 last-write-wins로 교체하지 않는다.
- Taxonomy validate/impact response의 current profile-policy/registry와 Organization/Platform catalog snapshot 및
  profile-policy response의 current taxonomy/registry와 두 catalog snapshot을 publish가 모두 CAS한다. Topic A deprecate
  taxonomy publish와 A mapping 추가 policy publish를 barrier로 경합시키면 호환 가능한 winner 하나만
  commit되고 loser는 fixed snapshot conflict다. Incompatible current pair와 partial audit는 남지 않는다.
- Current-validation과 taxonomy/registry publish 또는 assignment replacement가 경합하면 exact current
  pointer와 assignment revision을 모두 만족한 command만 commit된다. Locked manual assignment도
  effective 값을 바꾸지 않는 validation은 가능하지만 stale command는 lock 여부와 무관하게 `409`다.
- Current-validation이 current content를 읽은 뒤 source sync가 새 canonical input을 commit하는 race와 반대
  commit order를 검증한다. Exact content revision과 assignment revision을 같은 transaction에서 검증한 winner만
  manual-confirmation provenance를 남기며 loser는 stale conflict로 last-validated ref/audit/reindex projection을
  만들지 않는다. Source-managed content-confirming validation은 source authorization revision/watermark revoke와
  양쪽 commit order를 추가로 경합시키며 authorized serialization winner만 commit한다. Automatic sync/worker는
  content-review reason으로 current-validation을 호출할 수 없다.
- Taxonomy-less type-only assignment와 최초 taxonomy publish 경합은 nullable current pointer를 같은
  직렬화 경계에서 검증한다. Assignment가 먼저 commit되면 뒤의 publish는 assignment를 자동 변경하지 않고
  새 current 기준 stale/current-validation-required projection을 만들며, publish가 먼저 commit되면 stale
  null assignment request는 mutation/audit 없이 conflict다.
- Empty-topic assignment의 explicit current-validation은 assigned taxonomy가 null 또는 이전 published
  version인지와 최초 taxonomy version의 empty/non-empty 여부에 관계없이 invalid-topic review로 바꾸지 않고
  effective set과 axis별 source/lock/assigned snapshot을 보존한 채 last-validated taxonomy ref만 전진시킨다.
  Null-taxonomy type-only assignment 뒤 최초 empty publish와 prior-taxonomy empty-topic assignment 뒤 empty
  successor는 모두 topic axis를 `current_validation_required`, `resolver_eligible=false`,
  `review_required=false`로 projection한다. 반대로 non-empty topic assignment 뒤 empty successor를 publish하면
  missing topic 때문에 `review_required`이고 current-validation으로 ref를 전진시키지 않는다. 이미 current면
  unchanged다.
- Suggestion accept가 effective set을 바꾸지 않는 경우 outcome과 assignment/audit transaction의
  idempotency를 검증한다. Network retry가 duplicate audit나 duplicate reindex job을 만들지 않는다.
- Suggestion reject는 assignment revision과 axis별 source/lock 및 reindex projection을 바꾸지 않고 rejected outcome과
  bounded canonical audit만 같은 transaction에서 확정한다. Audit flush/commit 실패는 outcome을 rollback하고
  exact terminal retry는 duplicate audit를 만들지 않는다. Unknown/free-text reject reason은 schema validation에서
  거부한다. 같은 `suggested` row의 accept/reject barrier race는 terminal winner 하나와 canonical audit 하나만
  만들고 loser가 assignment 또는 반대 outcome을 commit하지 않는다.
- Mixed-axis assignment에서 suggestion candidate가 topic만 제안하면 `accepted_axes=[topics]`는 manual
  document-type value/source/lock을 보존하고 topic axis만 `accepted_suggestion`으로 전이한다. Candidate 밖 axis,
  empty/duplicate/unknown axes와 absent assignment를 complete하게 만들 수 없는 candidate는
  `knowledge.classification_suggestion_axes_invalid`, manual/locked selected axis와 stale nullable assignment
  revision은 각각 fixed conflict로 mutation 없이 거부한다.
  Subset 성공은 suggestion 전체를 terminal accepted로 만들고 provenance에 accepted axes를 남기며 선택하지
  않은 proposed axis가 suggested child 또는 later retry로 다시 적용되지 않는다.
  Manual PUT의 Client-supplied partial payload는 여전히 허용하지 않는다.
- Suggestion list는 state별 기본 25/최대 50 keyset page와 stable
  `created_at DESC, suggestion_id DESC` ordering을 사용한다. 0, 1, 25, 50, 51개, 동일 timestamp, page 사이
  expire/purge/insert, empty next page를 검증하고 duplicate/skip, unbounded query와 total/hidden count를
  허용하지 않는다. Scope/state가 다른 cursor, malformed/oversized cursor는 원문 비반사 fixed `422`다.
- Taxonomy와 profile-policy version list는 bounded cursor pagination과 stable
  `created_at DESC, version_id DESC` tie-break를 사용하고, organization profile identity/revision list는 각각
  `created_at DESC, profile_id DESC`와 `created_at DESC, revision_id DESC`를 사용한다. 동일 timestamp, empty page,
  stale cursor, draft/published/superseded 혼합, version detail 재조회와 다른 organization resource safe `404`를
  검증한다. Draft create response를 잃은 뒤 list/detail로 동일 draft를 복구할 수 있어야 한다.
- Taxonomy/profile/profile-policy version create/PATCH/publish/deprecate와 canonical audit는 같은 transaction이다. Audit
  저장 실패는 version mutation/current pointer를 rollback하고 list/detail, validate와 impact preview는
  mutation audit를 만들지 않는다.
- Override options의 Organization/Platform 항목과 valid GET은 strict
  `profile_revision_ref={catalog_scope: organization|platform, profile_revision_id}`를 반환하고 PUT은 선택한
  ref 전체를 그대로 round-trip한다. 두 catalog에 같은 opaque revision ID가 있는 fixture도 `catalog_scope`에
  따라 각각 정확한 target과 GET의 nullable `resolved_profile_revision_ref`로 resolve된다. Ref 자체의
  null/배열/문자열/숫자/boolean과 field별 null/배열/object/숫자/boolean, missing/unknown/non-string
  `catalog_scope`, empty/non-string `profile_revision_id`, bare `profile_revision_id`와 extra ref field는 profile
  repository 조회 전
  `knowledge.processing_profile_override_invalid`이고 mutation/audit가 없다.
  Cross-organization Organization ref는 lifecycle/selectability보다 먼저 ownership-first safe `404`로 닫는다.
- Override set/change는 Organization scope의 active owner published/selectable revision과 Platform scope의 approved
  published/selectable revision만 허용한다. Same-scope deprecated/non-selectable target은 fixed lifecycle error로,
  cross-organization target은 ownership-first safe `404`로 override/audit 없이 거부한다.
- Override set/change/clear는 required nullable assignment/profile-policy와 override expected revision 및 opaque
  full resolver revision을 모두 검증한다. 같은 revision의 concurrent set은 하나만 성공하고 stale set/clear는
  override, audit와 derived `reindex_required` projection을 전부 rollback한다. Set/valid-clear 성공 audit은 safe
  `catalog_scope`와 profile revision ref만 포함하고 raw config를 포함하지 않는다. Missing/deprecated/incompatible/
  cross-organization invalid current override clear audit은 KB-owned override revision과 fixed unavailable state만
  포함하고 hidden target profile scope/ID를 포함하지 않는다. 각각 canonical audit 한 건만 만들고 retry가
  duplicate audit를 만들지 않는다.
- Assignment 또는 current profile-policy가 없는 platform-default resolver에서 override first set/clear GET과
  reindex preview/admission은 required nullable assignment/profile-policy revision, exact Platform default ref와
  catalog revision을 반환한다. Set/clear request는 nullable values를 각각
  `expected_assignment_revision`, `expected_profile_policy_version`으로 보내며 opaque resolver token만 보내거나
  nullable field를 생략하면 schema validation에서 거부된다. Null을 생략하거나 sentinel로 바꾸지 않고 snapshot
  뒤 최초 pointer가 생기거나 Platform default ref/catalog revision이 바뀌면 override/admission은 stale conflict로
  전체 rollback한다. Current-validation과 lock/unlock은 existing assignment가 필요하므로 null을 허용하지
  않는다.
- Override GET에서 `expected_profile_policy_version=null`을 받은 뒤 최초 policy publish와 set/clear를 barrier로
  경합시키면 publish가 먼저 commit된 request는 opaque token이 있더라도
  `knowledge.processing_profile_override_snapshot_conflict`이고 override/audit/reindex projection을 남기지 않는다.
  Existing policy version 변경·제거와 반대 commit order도 explicit field와 full vector를 같은 transaction에서
  검증해 winner 하나만 만들며 resolver token이 explicit pointer precondition을 대체하지 않는다.
- Override GET의 opaque resolver revision은 canonical content, assignment, current registry/taxonomy, mapping
  policy, nullable override, Platform default ref, Organization/Platform profile catalog/selectability와 resolved
  profile exact revision을 모두 organization/KB/actor와 함께 bind하고 capability가 아니다. 다른
  organization/KB/actor의 token 재사용은 override/profile identity를 노출하지 않는 fixed snapshot conflict이며
  mutation/audit가 없다. Set과 clear 각각에 대해 vector input 하나씩을 GET 뒤 commit 전에 바꾸는 table-driven
  barrier test에 nullable policy pointer와 Platform default ref/catalog revision도 독립 case로 포함한다. Stale
  request는 `knowledge.processing_profile_override_snapshot_conflict`로 override, audit와 `reindex_required`
  projection을 모두 rollback한다.
- Override set/clear와 content/policy/catalog change가 반대 commit order로 경합해도 성공한 mutation의
  `reindex_required`는 같은 transaction에서 검증한 final vector로 계산돼야 한다. 이전 resolver 결과와
  새 override를 혼합한 projection, last-write-wins 성공 또는 자동 reindex job을 허용하지 않는다.
- Cross-organization suggestion/profile/policy/override ID를 accessible KB request에 섞어도 ownership
  lookup이 terminal/deprecated/incompatible/revision validation보다 먼저 실행된다. 모든 조합은 동일한
  safe `404`이고 semantic `409`, label, state 또는 expected revision을 노출하지 않는다.
- Legacy KB에 independent Document가 둘 이상이면 KB-scoped mutation은
  `knowledge.classification_migration_required`이며 exact document/version transitional route만
  동작한다. Active pointer만 보고 sibling 전체를 대표한다고 추론하지 않는다.
- Transitional route의 document가 target KB에 속하지 않거나 current version과 불일치하면 hidden
  또는 stale conflict로 닫고 다른 document assignment를 변경하지 않는다.
- Safe GET response는 stable IDs, approved bounded labels, revisions, axis별 source/lock/freshness/resolver-eligible와 reindex state만
  포함한다. Raw content/excerpt, source title/path/URL, prompt/completion/rationale, credential,
  provider raw response, hidden candidate ID와 exact denied count가 직렬화되지 않는다.
- Classification options의 current assignment가 `current_validation_required`이면 server-computed bounded
  `current_validation_changed_dimensions`와 generic `can_confirm_current_content`를 반환한다. Empty/single/pair/all
  changed-set projection을 검증하고 raw content/hash/source identity를 포함하지 않는다. Options 뒤 snapshot,
  `content_read` 또는 source/display authorization이 바뀌면 submit은 값을 capability로 사용하지 않고 current state를
  다시 검증해 stale/denied로 닫는다.
- Classification, suggestion, profile options/override와 profile preview response는
  `Cache-Control: no-store`를 사용한다. Preview resolution source enum은 null-policy exact default에서만
  `platform_default`를 반환하며 default ref는 read-only이고 Client가 Platform default mutation control을 만들지 않는다.
- Taxonomy/profile-policy manager list/detail과 authoring response도 `Cache-Control: no-store`를
  사용한다.

### Processing Decision, Artifact Build And Activation Tests

- 같은 canonical bytes/structure/materialization-safe metadata와 canonicalization contract는 동일
  canonical materialization input hash를 만든다. Source sync generation만 바뀌고 input hash가 같으면
  lineage ref만 전진하고 새 canonical content revision 또는 reindex를 만들지 않는다.
- 같은 canonical materialization input hash와 normalized materialization configuration은 동일
  materialization input fingerprint를 만든다. Reindex 산출 DocumentVersion/index ID, 결과 bytes,
  ordering 차이와 unordered topic set은 pre-build fingerprint를 바꾸지 않으며 동일 입력 재시도는 새
  output ID 때문에 연속 reindex되지 않고 수렴한다.
- Resolver input fingerprint는 assignment, current taxonomy/registry, nullable mapping policy, nullable override,
  Platform default ref, Organization/Platform profile catalog/selectability와 resolved exact scoped profile ref를
  모두 포함한다. Taxonomy rename이나 materialization output을 바꾸지 않는 default pointer 변경처럼 resolver
  revision만 바뀌고 materialization configuration이 같으면 resolver fingerprint만 바뀌고 materialization input
  fingerprint와 `reindex_required`는 유지된다. Deprecation이나 resolver branch 전환으로 resolved scoped profile ref가
  바뀌면 두 input fingerprint가 모두 바뀐다.
- Assignment-derived topic/type field가 chunk/index metadata에 materialize되는 configuration에서는 해당
  normalized field 변경이 materialization input fingerprint도 바꾼다. Active-job dedupe는 canonical
  content revision ref와 두 input fingerprint가 모두 같을 때만 허용하고 일부만 같은 job을 current
  decision으로 재사용하지 않는다.
- Artifact integrity hash는 build 뒤 normalized chunk/representation/index manifest와 embedding
  completeness에서 계산한다. Output corruption은 검출하지만 이 hash를 admission job identity나
  pre-build dedupe 입력으로 사용하지 않는다.
- Decision/Build Manifest는 각각 exact immutable refs를 저장하고 Decision Satisfaction은 append-only다.
  `latest`, mutable label/path, raw model score와 client-supplied resolved profile ref는 provenance가 될 수 없다.
- Resolver-only 변경에서 materialization input fingerprint가 active build와 같고 integrity가 valid하면
  새 Decision Manifest와 `satisfied_existing` link를 만들고 current decision pointer만 바꾼다. Active
  artifact pointer, DocumentVersion/index와 durable build job은 그대로다.
- Same canonical content ref와 두 input fingerprint decision이 이미 current면 unchanged이며 repeated
  idempotency key와 concurrent same-decision request가 duplicate manifest/satisfaction/job을 만들지 않는다.
- Reindex admission wire는 exactly one required `Idempotency-Key` header를 사용한다. Header name의 case variant는
  같은 field로 허용하지만 값은 non-nil lower-case hyphenated UUID canonical 36 ASCII character 하나여야 한다.
  Missing, duplicate, nil UUID, upper-case, leading/trailing whitespace, braces, `urn:uuid:` alias, comma-joined
  값, malformed/다른 길이와 body-only idempotency field는 receipt repository 접근 전에
  `422 knowledge.processing_idempotency_key_invalid`이고 receipt/audit를 만들지 않는다. Server가 alias를
  trim/lower-case/unwrap하거나 duplicate 중 하나를 선택하면 실패다.
- Valid key는 canonical ASCII 36 byte의 SHA-256 digest만 organization/KB/actor/operation scope에 저장하고
  typed request tuple의 canonical digest는 별도 column/value로 검증한다. Raw key가 response, validation detail,
  application/access log, audit, trace, task payload와 DB row에 없고 known fixture digest가 byte-for-byte 일치하는지
  확인한다.
- 같은 scoped key와 exact typed request tuple의 sequential/concurrent retry는 original safe receipt result로
  수렴하고 duplicate decision/satisfaction/job/audit를 만들지 않는다. 같은 scoped key에 preview token 또는
  어느 typed field라도 다른 request는 `409 knowledge.processing_idempotency_conflict`이며 기존 result identity를
  노출하거나 덮어쓰지 않는다.
- Successful admission 응답을 잃은 뒤 `resolution_revision` TTL이 지난 exact same actor/key/request replay는
  parent ownership, current KB `write` 또는 Organization manager authority와 applicable fresh source
  authorization을 receipt lookup 전에 확인한다. Gate를 통과한 request만 durable receipt를 찾아
  `unchanged/satisfied_existing/job_created/job_reused` result를 반환하며 token 만료 conflict나 새
  decision/satisfaction/job을 만들지 않는다. Source-managed denied/revoked/stale/unknown은 receipt repository
  조회와 result projection 전에 `404 resource.hidden`이고 receipt/result/job identity를 노출하지 않는다.
  Manual KB의 `not_applicable` source gate는 replay를 막지 않는다.
- Active artifact로 충족되는 최초 admission과 exact receipt replay는 모두 public
  `status=satisfied_existing`을 반환한다. Public schema, receipt와 Client contract에
  `*_artifact` alias가 나타나지 않으며 `unchanged/job_created/job_reused`도 최초
  응답과 replay에서 byte-equivalent safe result enum을 유지한다.
- Receipt가 없는 expired token은 `knowledge.processing_preview_stale`이고, 같은 key의 다른 token/body/
  canonical request는 `knowledge.processing_idempotency_conflict`다. 다른 actor 또는 권한이 revoke된 actor는
  receipt 존재와 result identity를 알 수 없으며 기존 result를 받지 못한다. Receipt는 raw token/internal
  fingerprint 없이 job/result와 같은 기본 30일 retention 뒤에만 cleanup된다.
- Source-managed KB에 successful receipt가 있는 상태에서 source authorization을 revoke한 뒤 exact replay를
  호출하면 source gate가 receipt lookup보다 먼저 실행되고 safe `404`다. Receipt/result status, job identity와
  기존 success audit metadata를 response로 반환하지 않는다. Regrant 뒤 새 fresh gate가 통과하면 같은 exact
  replay가 original result를 read-only로 복구할 수 있지만 receipt나 audit를 새로 만들지 않는다.
- Source gate allowed 결과를 얻은 뒤 receipt read/return 또는 fresh admission commit 직전에 revoke revision을
  barrier로 commit한다. Revoke winner는 `unchanged`, `satisfied_existing`, `job_created`, `job_reused` 각각에서
  safe `404`이고 신규 receipt/manifest/satisfaction/current decision pointer/job/outbox 및
  `knowledge.processing_reindex.admitted` success audit가 0개다.
  특히 active artifact를 찾은 `satisfied_existing` path가 stale source decision으로 current decision pointer를
  바꾸지 않는지 검증한다. Admission winner가 먼저 authoritative CAS/commit한 반대 순서는 result와 audit 하나만
  남기고 후속 worker/finalizer gate가 revoke를 처리한다.
- 네 최초 successful result를 table-driven으로 실행해 모두 `action=knowledge.processing_reindex.admitted`,
  `audit_logs.status=success`, allowlisted `result_status` 하나를 receipt 및 result-specific write와 같은
  Unit of Work에 정확히 한 번 저장한다. `unchanged`는 receipt+audit만, `satisfied_existing`은 required
  decision/satisfaction/current-pointer+receipt+audit, `job_created`와 `job_reused`는 각 계약 write+receipt+audit가
  원자적이어야 한다. Audit flush/commit failure는 receipt를 포함한 모든 admission write를 rollback한다.
- 각 result의 exact authorized replay와 concurrent same-key retry는 original status를 반환하지만 추가
  `knowledge.processing_reindex.admitted` audit를 만들지 않는다. Idempotency conflict, preview stale와
  source-hidden failure에도 이 success action이 없다. Source-hidden path에 ADR-0017 request-scoped security audit를
  선택적으로 기록하면 target/source ID, receipt/result/job identity와 exact denied state를 포함하지 않아야 한다.
  Failure audit metadata에는 idempotency key/token digest, internal fingerprint, source identity, raw permission 또는
  revoked grant detail이 없다.
- Materialization input fingerprint 변경은 create-only staging DocumentVersion/index를 만든다. Existing
  active chunks/index를 delete/update한 뒤 새 artifact를 쓰는 순서를 사용하지 않는다.
- Parser, chunk, embedding, representation, smoke validation, DB finalization, outbox insert 중 어느
  단계가 실패해도 이전 active ready version이 retrieval-visible하고 pointer는 그대로다.
- Ready validation 전 staging/superseded/failed representation과 chunk는 picker, recommendation,
  vector/keyword retrieval, citation과 answer context에 나타나지 않는다.
- Reindex worker authorization barrier를 missing/deactivated actor, active membership 회수, effective KB
  direct/team `write` 회수, Organization manager 강등과 applicable source authorization revoke 각각에 적용한다.
  Direct/team grant 중 하나를 회수해도 다른 `write` grant가 남거나 manager 강등 뒤 별도 `write`가 남으면
  계속 허용하고, 마지막 effective authority를 잃은 경우만 cancel한다. Revoke가 external batch gate 전에
  commit되면 해당 source/provider adapter call은 0회다. Gate 뒤 in-flight bounded call 중 revoke가
  commit되면 그 call은 끝날 수 있지만 다음 batch는 0회이고 finalizer는 Satisfaction과 current
  decision/active artifact pointer를 만들지 않는다. Current fence/lease가 유효하면 fixed
  `knowledge.processing_execution_not_authorized`의 terminal `cancelled`와 owned staging cleanup intent만
  commit하고, fence를 잃었으면 cancellation/cleanup도 commit하지 않는다. Authorization lookup session이
  external I/O 동안 열린 채 유지되거나 raw source/permission detail이 status, audit 또는 trace에 남으면 실패다.
- Physical build admission은 exact profile model/provider에 대해 same-organization active credential, verified
  relation과 job execution actor `use`를 만족하는 후보 수를 0/1/2로 만든 table-driven test를 사용한다. 1개일
  때만 safe credential/model/provider refs와 lifecycle/relation/permission/provider-routing revision을 가진 binding을
  job/attempt에 고정하고 provider를 호출할 수 있다. 0개와 2개는 모두
  `409 knowledge.processing_embedding_credential_unavailable`이며 name/order/priority/owner/default fallback,
  receipt/decision/job/success audit와 provider call이 0개다. Response/audit/trace에는 credential ID, 후보 수,
  relation/permission detail, config/secret가 없다.
- Exact-one binding issue와 fresh job/receipt commit 사이에 credential revoke, relation/permission/provider-routing
  revision 변경을 barrier로 주입한다. Change transaction이 먼저 commit되면 stale admission은 fixed 409로
  receipt/decision/job/success audit를 하나도 남기지 않고, admission serialization winner만 binding과 job을 함께
  commit한다.
- Bound credential revoke/invalid, verified relation 제거·revision 변경, actor의 마지막 effective credential `use`
  회수와 provider-routing revision 변경을 각각 claim/next provider batch/finalization barrier에서 경합시킨다. Gate
  전에 변경이 commit되면 해당 adapter call은 0회이고, in-flight bounded call 뒤 변경이면 다음 batch와
  finalizer pointer swap이 0회다. Valid fence/lease에서는 fixed
  `knowledge.processing_embedding_credential_unavailable` terminal `cancelled`와 owned staging cleanup intent만
  commit하고 기존 active-ready artifact를 유지한다. Gate/finalization session은 provider I/O 동안 열어 두지 않는다.
- Actor A가 `job_created`, actor B가 같은 active decision에서 `job_reused` receipt를 받은 뒤 A의 authority만
  회수하는 case는 B의 현재 권한과 무관하게 기존 job을 cancel하고 execution actor를 B로 바꾸지 않는다.
  B가 terminal cancellation 뒤 fresh preview와 새 idempotency key로 admission하면 새 generation 하나만
  생성되고, A/B의 기존 receipt replay가 cancelled job을 재활성화하거나 execution actor를 바꾸면 실패다.
- Actor A의 job binding이 credential revoke로 invalid해진 뒤 credential 사용 권한이 있는 actor B가 같은 active
  job을 reuse하면 safe `job_reused` result가 B의 credential을 선택하거나 binding을 교체하지 않는다. 다음 worker
  gate가 existing generation을 terminal cancel하고, B는 그 뒤 fresh preview/new idempotency request로 새
  generation과 새 exact-one binding을 받아야 한다.
- Finalization authorization check와 permission/source authorization revoke를 barrier로 경합시켜 revoke가 먼저
  직렬화되면 cancellation만, finalization이 먼저 직렬화되면 authorized pointer swap만 commit되는지 검증한다.
  어느 순서에서도 cancelled job의 `satisfied_new`, duplicate cleanup intent 또는 unauthorized next batch가 없어야 한다.
- Finalizer는 canonical content revision/hash, assignment, current document-type registry, current
  taxonomy, mapping policy, nullable override, Organization/Platform profile catalog/selectability, resolved scoped profile ref,
  current execution/source authorization과 embedding binding lifecycle/relation/permission/provider-routing revision,
  job fencing/lease와 artifact completeness를 같은 transaction에서 다시 resolve하고 두 input
  fingerprint를 재계산한다. 각 resolver input을 하나씩 바꾸는 table-driven race에서 stale worker는
  새 decision/artifact pointer, Decision Satisfaction 또는 content hash를 commit할 수 없다. 유효한
  fence/lease를 가진 worker는 terminal stale transition과 자신이 소유한 staging artifact cleanup outbox
  intent만 원자적으로 commit한다. Fresh re-admission은 current vector로 다시 판단하고 materialization fingerprint가 active
  build와 같으면 `satisfied_existing`으로 수렴한다.
- Fence/lease를 잃은 stale worker는 terminal state나 cleanup intent를 commit하지 못한다. Reconciler는
  orphan/terminal staging attempt의 누락·미완료 intent를 생성하고, handler는 generation/fence와 current
  active/decision reference 부재를 확인한 뒤에만 DB/vector/storage artifact를 idempotent 삭제한다. Cleanup
  retry 또는 crash가 active artifact를 삭제하거나 duplicate destructive effect를 만들지 않는다.
- Historical Decision Satisfaction/Artifact Build Manifest만 참조하는 superseded artifact는 provenance reference
  때문에 영구 pin되지 않는다. Retention이 만료되고 current active/current decision, in-flight build,
  citation/evidence retention과 legal hold pin이 모두 없으면 cleanup 대상이 된다. 각 blocking pin을 하나씩 둔
  table-driven case는 physical delete와 tombstone을 만들지 않는다.
- Eligible cleanup은 pre-delete transaction에서 generation-fenced `purging` state, durable intent와 첫
  `assignment_impact_snapshot_revision`을 먼저 확정한다. 이 transaction 또는 intent 저장 실패는 external delete
  호출 전에 전체 rollback한다. `purging` artifact를 retrieval, citation 또는 `satisfied_existing` 후보로 선택하지
  않고 physical object/vector/index delete 성공 뒤 completion transaction에서 append-only Artifact Availability
  Tombstone 하나, `purged` availability, 다음 impact revision과 canonical
  `knowledge.processing_artifact.purged` audit를 함께 확정한다. Manifest, integrity hash와 Satisfaction은
  immutable provenance로 남지만 dereference/reuse할 수 없다.
- 과거 manifest ID 또는 Decision Satisfaction ref를 직접 제시하는 내부 호출도 current availability projection과
  tombstone을 확인해야 한다. `purged` ref는 lineage 조회에서만 보이고 retrieval/citation/reuse 경로에서는 fixed
  unavailable 결과로 닫혀 stale provenance reference가 physical retention이나 재사용 capability가 되지 않는다.
- Physical delete 전 crash는 retry가 같은 generation으로 delete를 재시도한다. Delete 성공 뒤 tombstone/audit
  completion commit 전 crash 또는 audit flush 실패는 `ready`로 rollback하지 않고 `purging`과 durable intent를
  유지한다. Reconciler는 storage absence와 generation을 확인해 tombstone/`purged`/impact revision/audit를 같은
  transaction에서 완성한다. Concurrent pin 생성/current pointer swap과 cleanup race는 serialization winner에 따라
  delete 전 pin을 존중하거나 이미 `purging`인 artifact를 새 current/citation 대상으로 선택하지 않는다. Exact
  retry가 duplicate tombstone/`knowledge.processing_artifact.purged` action 또는 다른 generation artifact 삭제를
  만들지 않는다.
- Finalizer가 current pointer/vector를 읽은 뒤 swap하기 전 taxonomy/registry/policy/override publish가
  경합하는 barrier test에서 row lock/CAS/serializable winner 하나만 commit된다. Loser는 stale이며 old
  comparison 결과로 pointer를 교체하지 않는다.
- Current decision/active artifact pointer swap, previous supersede, canonical success state와 cleanup outbox는 기존
  ADR-0017/ADR-0052 transaction 계약을 따른다. Outbox worker가 실패해도 새 active version은
  유지하고 cleanup을 retry한다.
- Policy publish, migration 또는 code deployment만으로 all-KB reindex가 시작되지 않는다. Explicit
  bounded admission이나 approved rollout 없이 durable job 수가 증가하면 실패다.
- 새 profile/representation field가 없는 legacy KB는 existing legacy/general path로 검색된다.
  Missing target table/row를 empty evidence, 500 또는 destructive auto-backfill로 처리하지 않는다.
- MBA-310 representation이 disabled/missing/failed인 KB는 approved canonical/legacy representation
  fallback을 사용한다. Derived representation failure가 canonical evidence를 삭제하지 않는다.
- MBA-311 diversification이 disabled 또는 policy evaluation 실패 시 prior stable candidate ordering으로
  fallback한다. Feature enablement가 retrieval availability를 차단하지 않는다.

### Privacy, Audit And UI Tests

- Classifier fixture와 captured logs/audit/trace/task result/dead-letter에는 raw source content,
  prompt/completion/rationale, API key, credential config와 provider raw exception이 없어야 한다.
- Suggestion durable record는 bounded candidate IDs, version refs, calibrated state, safe reason과 outcome만
  저장한다. Accepted/rejected outcome은 explicit organization training policy 없이 training export에
  포함되지 않는다.
- Suggestion accept는 assignment revision, terminal outcome과 canonical audit를 한 transaction에서
  확정하고 opaque suggestion ref, accepted axes, `generator_kind`와 `generator_contract_ref`를 immutable safe
  snapshot으로 남긴다. `deterministic_rule`은 approved rule-set/version provenance만 보존하고 classifier
  policy, model, prompt-template, calibration과 confidence field가 없어야 한다. `ai_classifier`만 classifier
  policy, prompt-template contract, model catalog/effective model, calibration revision/state와 bounded
  confidence bucket을 추가한다. Audit 실패는 assignment/outcome/snapshot을 모두 rollback한다.
- Suggestion은 생성 후 30일 경계부터 review할 수 없고 expired가 된다. Terminal/expired 전이 후
  30일 경계부터 operational row purge 대상이며 assignment revision과 canonical audit은 함께
  삭제되지 않는다. Content/taxonomy change는 30일 전에 즉시 expire시킬 수 있다.
- Accepted suggestion operational row를 purge한 뒤에도 assignment/audit의 safe contract snapshot과
  accepted outcome은 조회·재현 가능하다. Purge cascade 또는 FK 정리로 snapshot을 null/delete하지 않고 raw
  prompt/completion/rationale, raw score, provider response와 content는 snapshot·audit에 존재하지 않는다.
- Audit before/after는 stable IDs와 revisions만 사용하고 unbounded label/description, source path/title,
  document excerpt를 저장하지 않는다. Cross-org/hidden denial audit가 target identity를 생성하지 않는다.
- Profile override set/clear audit는 safe profile/override revision과 before/after state만 포함하고 raw
  parser/provider/profile config를 저장하지 않는다. Audit 저장 실패는 override mutation을 rollback한다.
- Canonical action registry는 FR-150b와 위 operation-to-action table이 확정한 22개 exact string 전체를
  ADR-0008, `AuditAction`, backend audit search allowlist와 Client filter/label mapping에서 table-driven으로
  대조한다. 22개 중 일부만 등록하거나 alias/combined wildcard/endpoint별 과거형 변형을 사용하면 실패다. 일반
  mutation의 audit write 실패는 전체 rollback하고 exact terminal/replay retry는 duplicate action을 만들지 않는다.
  Artifact purge completion audit 실패는 이미 완료된 physical delete를 rollback하지 않고 non-retrievable
  `purging`에서 same-generation retry로 정확히 한 action에 수렴한다.
- Read-only UI는 mutation control을 숨기되 API authorization을 대체하지 않는다. Write/manage 상태가
  바뀐 stale tab의 mutation은 server에서 다시 거부한다.
- Deprecated topic은 current assignment provenance에서 visible/disabled 상태로 남고 신규 picker,
  ranking boost와 profile mapping에서는 선택할 수 없다. Replacement를 자동 선택하지 않는다.
- Uncalibrated suggestion은 confidence percentage로 표시하지 않는다. Abstained/expired suggestion을
  accept control로 노출하지 않는다.
- Manual authority가 current인 axis는 suggestion accept control을 비활성화한다. Candidate를 manual
  assignment form에 채우는 동작은 가능하지만 사용자 확인 없이 submit하거나 authority를
  `accepted_suggestion`으로 낮추지 않는다.
- Type/topic source가 다른 assignment에서 UI는 각 axis의 source/lock을 별도로 표시한다. Manual type과
  rule topic 조합에서 topic-only suggestion accept는 활성화할 수 있지만 type axis를 함께 전송하거나
  manual source를 accepted-suggestion으로 낮추지 않는다.
- Manual edit UI는 complete effective set을 보내되 실제 edited axis만 `manual_axes`에 넣는다. Rule 또는
  accepted-suggestion source의 locked type과 unlocked topics 상태에서 topic edit는 type value/source/lock을 prefill해
  보존하고 topic만 selected한다. 반대 조합도 대칭이며 Client가 두 axis를 자동 selected하거나 locked axis를
  manual로 표시하면 실패다.
- Suggestion UI는 cursor page를 append하면서 stable ID로 dedupe하고 state/scope 변경 시 이전 cursor와
  in-flight response를 폐기한다. Next page 또는 review에서 `resource.hidden`이 반환되면 기존 candidate,
  confidence bucket과 count 추정치를 DOM/state에서 제거하고 source identity를 설명하는 오류를 만들지 않는다.
- Reviewer UI는 selected axes 또는 current assignment가 바뀔 때 accept-preview를 새로 요청하고 fresh
  `accept_preview_revision`을 받기 전까지 accept를 비활성화한다. Per-axis value/authority change, profile
  resolution change, reindex required와 active-ready availability를 각각 표시하되 exact hidden count/cost나
  완료·성능 향상을 암시하지 않는다. Accept는 화면에 표시한 axes와 같은
  `expected_accept_preview_revision`을 전송한다.
- Accept-preview/accept의 `knowledge.classification_suggestion_accept_preview_stale`은 cached token과 impact를
  폐기하고 current assignment/suggestion을 다시 읽게 하며 자동 accept하지 않는다. `resource.hidden`은 candidate와
  impact를 DOM/state에서 제거한다. Client storage, URL, telemetry와 error message에 preview token/digest를
  남기지 않는다.
- `409` stale response에서 Client는 current assignment/taxonomy를 다시 불러오고 local topic set이나
  lock을 자동 merge/retry하지 않는다.
- Client는 `current_validation_required`에만 current-validation action을 표시하고 `review_required`에는 current
  options 기반 complete assignment replacement를 표시하고 invalid axis만 `manual_axes`에 넣는다. Valid한 반대
  axis의 value/source/lock은 보존한다. Missing/deprecated ID를 자동 선택하지 않으며 profile
  override review-required는 replace/clear recovery로 연결한다. Locked classification review-required는 manager
  unlock 뒤 replacement 순서를 표시하고 write-only actor에게는 manager remediation 필요 상태를 표시한다. Lock은 current axis에서만 활성화하고 assignment와
  canonical content revision을 함께 보내며 unlock은 stale/review-required 상태에서도 표시한다.
- Profile preview는 resolution source, fallback, exact safe refs, reindex-required와 bounded estimate를
  표시하지만 activation 완료나 성능 향상을 보장한다고 표현하지 않는다.
- Preview의 `resolution_revision`은 active organization/KB/actor/current canonical content와 내부 두 input
  fingerprint에 묶인 non-reversible opaque token이다. Raw content hash/fingerprint가 response, URL,
  storage, log에 나타나지 않는다.
- Receipt가 없는 fresh request에서 preview 뒤 canonical content, taxonomy/registry/policy/override 또는
  materialization config가 바뀌거나 token scope/expiry가 다르면 reindex request는
  `409 knowledge.processing_preview_stale`이고 새 decision/job을 만들지 않는다. Client는 token을
  capability/authority로 사용하지 않고 preview를 다시 조회한다.
- 이미 성공한 exact idempotency request의 응답만 유실된 경우 Client는 같은 key/request로 replay해 기존
  safe result를 복구한다. 새 key나 새 token을 섞지 않으며 receipt가 없거나 request conflict이면 current
  preview를 다시 조회한다.
- Profile override UI는 server options만 표시하고 선택된 option의 `profile_revision_ref` 전체와 GET의 nullable
  `resolved_profile_revision_ref`를 scope 손실이나 Client 재구성 없이 사용하며 set request에는 선택 ref 전체를
  보내고 set/change/clear 뒤 별도 reindex action을 유지한다.
  Invalid current override에서도 clear가 노출되고 set/clear는 GET의 nullable `profile_policy_version`을
  `expected_profile_policy_version`으로 돌려보낸다. Stale conflict는 current override/options/policy version을
  모두 다시 불러오고 opaque token만으로 자동 retry하지 않는다.
- Classification topic picker는 32개 선택 상태를 유지하되 33번째 추가를 비활성화하고 server의 422를 generic
  validation state로 처리한다. Client 제한을 우회한 request도 API가 같은 bound로 거부하며 32개를 임의로
  잘라 저장하지 않는다.
- Taxonomy/profile catalog/profile-policy 관리 화면은 identity/version list/detail로 기존 draft와 immutable history를 다시
  열 수 있다. 다른 organization ID의 generic unavailable response에서 존재나 state를 추정하지 않는다.
- Taxonomy/profile/profile-policy editor는 exact `draft_revision`을 모든 mutation/validation/preview에 보내고
  stale conflict에서 local draft를 자동 merge/retry하거나 newer server draft를 덮어쓰지 않는다.
- Taxonomy editor는 version detail의 complete forest를 사용하고 topic 1,000개/replacement edge 2,000개에서
  add control을 비활성화하되 server validation을 authority로 유지한다. 새 topic에는 UUID
  `draft_topic_key`만 만들고 stable `topic_id`는 server mapping에서 받는다. Successful response가 유실되거나
  stale conflict가 오면 local ID를 발급하거나 PATCH를 자동 replay하지 않고 draft detail에서 complete forest,
  current revision과 current-topic key-to-ID mapping을 다시 읽어 subsequent mutation을 server ID로 만든다. Topic 제거 성공 뒤 Client도 local mapping을
  제거하고 같은 key를 재사용하지 않는다. Server에 제거된 key가 다시 제출되면 새 stable ID가 발급되므로 old ID와
  같다고 가정하지 않는다. Published/history/assignment payload에 draft key가 남거나 1,000-topic detail을
  truncate/paginate해 일부 tree만 overwrite하면 실패다.
- Profile-policy editor는 validate/impact response의 `organization_profile_catalog_revision`과
  `platform_profile_catalog_revision`을 각각 expected publish field로 보낸다. 한 revision이라도 누락하거나 stale이면
  자동 retry하지 않고 Organization/Platform option과 compatibility snapshot을 모두 다시 불러온다.
- `current_validation_required` assignment의 current-validation action은 valid stable ID에서 last-validated refs만
  전진시키고 options의 server changed-dimension set에 맞춰 단일 변경 reason 또는 multi-change
  `combined_review`만 제출한다. `can_confirm_current_content`는 UI visibility hint일 뿐 authorization으로 저장하거나
  재사용하지 않는다.
  Content mismatch에서는 locked/unlocked axis freshness를 구분하고 effective `content_read`와 fresh source/display
  gate 및 explicit content confirmation 없이는 validation을 제출하지 않는다. Taxonomy/registry-only 변경은 content
  confirmation UI를 표시하지 않지만 source-managed resource gate가 hidden이면 validation action과 cached assignment를
  폐기한다. `review_required`에는 current-validation action을 노출하지 않고 current
  options 기반 complete replacement만 제공하며 missing/deprecated replacement ID를 자동 선택하지 않는다. Invalid
  axis가 locked이면 manager unlock 뒤 replacement 순서를 유지한다.
- Taxonomy impact preview는 `taxonomy_impact_bucket_v1`의 affected/reindex bucket만 표시하고
  `none=0`, `small=1..10`, `medium=11..100`, `large=101+` 경계 밖 exact count나 hidden
  KB/document identity를 노출·추정하지 않는다. Affected는 candidate/current taxonomy의 topic-axis freshness,
  eligible topic/primary tuple이 달라지는 processing-eligible current assignment이고
  reindex candidate는 그중 active-ready artifact의 비교 가능한 fingerprint가 current decision과 다르거나 active-ready
  legacy artifact에 current decision/fingerprint가 없어 동일 materialization을 증명할 수 없는 subset을 뜻한다.
  Profile revision/source/status만 달라 fingerprint가 같음이 증명되면 제외한다.
  Publish는 impact 0개도 contract version, opaque preview revision과 explicit acknowledgement를 요구하고
  version/expiry/snapshot conflict에서 old confirmation을 자동 재사용하지 않는다. Publish confirmation은 taxonomy
  version publish만 승인하며 assignment 변경이나 reindex job 시작을 완료된 동작으로 표시하지 않는다.
- Organization profile editor create는 exact `{}`만 보내고 null config/base, revision 0 response를 복구 가능한
  draft로 표시하며 complete config PATCH 전 validate/publish를 활성화하지 않는다. 이후 editor는 allowlisted
  token/parser/representation/embedding fields만 편집하고 platform profile과
  published config에 edit/delete control을 표시하지 않는다. Current policy/override 때문에 deprecate가 차단되면
  hidden reference identity/count 없이 safe remediation을 표시하고 성공 뒤에도 자동 reindex로 표현하지 않는다.

### Evaluation Gate Tests

- MBA-309 confirmatory run은 dataset/result 열람 전에 primary metric, 동일 context-token budget,
  minimum effect 또는 non-inferiority margin, maximum risk/minimum coverage, sampling/cluster/power,
  multiplicity와 stopping rule을 immutable manifest에 기록한다.
- Split은 document/source cluster 단위로 train/calibration/test leakage를 막고 document type과 query
  intent strata를 보고한다. 동일 문서의 current/history version이 서로 다른 split에 들어가지 않는다.
- Profile, contextual representation와 diversification은 하나씩 분리한 ablation과 필요한 interaction
  condition을 사용한다. 여러 factor를 동시에 바꾼 결과로 개별 원인의 우위를 주장하지 않는다.
- Production mapping/default는 human-reviewed holdout의 preregistered gate를 통과한 candidate만 승인한다.
  Exploratory MBA-279 winner, synthetic-only 만점 또는 point estimate만으로 활성화하지 않는다.

## MBA-333 Privacy Detection And Pre-Embedding Masking Tests

[Privacy Detector 보호 리소스 완결성 매트릭스](privacy-detector-protected-resource-completion.md)의
미완료 경계를 MBA-362가 TDD로 닫을 때 아래 시나리오를 최소 증거로 사용한다.

### Contract Trace

| 경계 | 정상 | 실패/경합 | 필수 증거 |
| --- | --- | --- | --- |
| authorization/content safety/parser | authorized source가 local parser 또는 approved raw-parser egress를 통과 | cross-Organization, source revoke, unsupported/active content, external parser readiness 부재 | raw upload/baseline/provider/embedding의 단계별 0회 |
| local hard baseline | local/external parser output 뒤 모든 mode의 첫 detector로 실행 | crash/timeout/unknown 또는 terminal block | failure/block 모두 organization provider 0회, raw-free exact safe reason |
| effective scoped policy | platform/Organization, 모든 explicitly bound Collection과 applicable source/KB strongest union | weaker override, scope-set race, 복수/cross-org provider | exact scope/Collection privacy/source/KB binding snapshot, Organization epoch invalidation, zero external call |
| mode/provider/action matrix | closed policy publish/activate validation | null/non-null cardinality mismatch, review readiness 부재 | zero policy activation, no downgrade |
| provider selection | exact same-Organization active revision | missing/inactive/revoked/cross-org/ambiguous | no fallback, no embedding |
| provider egress approval | exact same-Organization immutable approval + trust-tier operation profile | public guard 완화, private profile/network isolation 부재, missing/stale/expired/revoked | adapter 0회, no approval detail leak |
| provider-safe view | external provider에 baseline-free view | baseline secret canary, mapping gap/crossing span | outbound body 비노출, conservative map |
| text/span contract | exact NFC/LF, UTF-8 byte half-open spans | wrong fingerprint/coverage/boundary/cap | whole-result failure |
| local masking | additive union과 deterministic replacement | overlap/order/category/confidence 변형 | byte-identical canonical output |
| finalization | complete canonical/chunk/embedding/index | cancel/revoke/stale fence/partial artifact | current-valid old active만 유지, no pointer swap |
| current privacy validity | manifest의 exact platform/Organization validity revision refs+`validity_epoch`과 current 두 epoch | preserving same-epoch, platform/Organization invalidating +1, missing/malformed/stale epoch, stale cache/vector | CAS+audit commit 시점부터 old epoch evidence unavailable |
| observability | safe state/reason/bucket | raw/parser/provider exception/digest/span canary | all sinks 비노출 |
| rollout | explicit policy bootstrap와 immutable pre-cutoff legacy inventory | provider/review enable 미준비, client re-enrollment, 30-day max 초과, post-cutoff failure | enable fail-closed, one-way cutoff/hard max 뒤 unavailable |
| review source/expiry/cleanup gate | Organization manager + fresh source display authority + non-expired candidate | projection 전/commit 전 revoke, TTL equality, stale cleanup owner | hidden/non-mutable, mutation/audit 0건, current cleanup owner만 body purge+receipt |
| deployment preflight | runtime과 같은 retrieval-visible resolver | stale manifest, frozen/current validity epoch 불일치 legacy, compliant pointer 뒤 legacy, retired/expired legacy | preview `200` + safe blocked/허용된 inactive warning, active mutation `409 deployment.preflight.blocked` |

### Shared Privacy Core Unit Tests

- CRLF, CR과 LF가 `privacy_text_unicode_14_0_nfc_lf_v1`의 LF로 수렴한다.
- NFC/NFD로 표현한 같은 한글/라틴 text가 같은 normalized bytes가 된다.
- Emoji, variation selector, combining mark와 multi-byte 한글의 byte range를 정확히
  마스킹한다.
- Privacy normalizer는 ADR-0065 taxonomy normalizer처럼 NFKC, case-fold 또는 whitespace
  축약을 하지 않는다.
- Runtime Unicode data version이 14.0.0 contract를 제공하지 못하면 readiness/attempt가
  provider와 DB artifact write 전에 실패한다.
- Negative, zero-length, reversed, out-of-range, UTF-8 code point 중간 start/end span을
  전체 거부한다.
- Document/segment 시작·끝에 정확히 맞는 half-open span과 overlap segment의 동일 span을
  허용하고 document-level union으로 중복 제거한다.
- Baseline/provider span의 same/contains/contained/partial-overlap/adjacent-policy 경우를
  table-driven으로 검증하고 provider span이 baseline을 제거하거나 action을 낮추지 못한다.
- 입력 span 순서, segment completion 순서와 provider response ordering이 달라도 같은
  `[REDACTED]` canonical bytes와 manifest outcome을 만든다.
- Unknown category, NaN/Infinity confidence와 span/response cap 초과는 앞부분만 적용하지
  않고 `knowledge.detector_response_invalid`로 실패한다.
- Unicode property/fuzz test는 임의 text/span에서 crash 없음, output determinism,
  UTF-8 valid output과 baseline bytes 미노출을 검증한다.

### Provider Port And Egress Contract Tests

- Adapter는 provider의 UTF-16 code unit, Unicode scalar/code-point offset fixture를
  normalized document UTF-8 byte offset으로 정확히 변환한다.
- Token-only offset은 exact transmitted character boundary와 승인된 versioned tokenizer
  mapping이 없으면 provider readiness 또는 전체 response에서 거부한다.
- Adapter가 immutable local call context에 결속한 contract, opaque request binding, expected
  segment set 또는 실제 전송 bytes에서 계산한 segment/view fingerprint가 다르면 결과 전체를
  거부한다. Custom correlation field를 지원하지 않는 provider도 local binding으로 정상 처리하고,
  지원하는 provider의 forged/missing echo만으로 결과를 수락하지 않는다.
- `detector_contract_ref`가 expected provider revision과 다르거나 `rule_or_model_ref`가
  server-approved bounded namespace 밖이면 전체 response를 거부하고 raw provider 설명,
  model output 또는 unknown reference를 durable sink에 남기지 않는다.
- Missing/duplicate/unknown segment, `complete=false`, missing complete와 valid subset만
  있는 partial result를 모두 거부한다.
- Empty span은 exact binding/schema/coverage와 `complete=true`를 모두 만족할 때만
  성공이다.
- `external_approved` request body에는 baseline canary email/token/key, raw document
  fingerprint, Organization/internal policy/provider revision, source URL/path/title,
  principal/ACL과 credential이 없다.
- Provider-safe view나 detector response를 `clean`, compliance pass 또는 raw egress approval로
  표시/저장하지 않는다. Explicit Organization egress approval, purpose/processor/data-residency/
  retention contract 중 하나라도 missing/stale이면 outbound call이 0회다.
- Provider-safe view의 제거 구간을 가로지르는 span은 원문 covering range로 확장하거나
  전체 거부하며 안전한 subrange로 축소하지 않는다.
- `organization_private` exact segment는 same-Organization provider/policy와 exact current immutable
  private-boundary Detector Egress Approval Revision일 때만 허용한다. Approval은 purpose, processor/ownership·endpoint/
  network boundary, data residency, retention, no-training/no-secondary-use와 active/expiry/revoke를
  포함해야 하고 private IP/DNS/mTLS/credential만 있는 fixture는 outbound call 0회다.
- `external_approved`는 ADR-0067 public-address guard의 private/link-local/metadata 차단을 그대로
  유지한다. `organization_private`는 dedicated operation profile과 worker/network namespace가 exact
  server-owned host/port/CIDR 외 egress를 차단할 때만 호출한다. 모든 DNS result 중 하나라도
  loopback/link-local/cloud metadata/multicast/unspecified/public/미승인 private address이면 0회다.
  Validated address pinning, peer/Host/TLS SNI, HTTPS+mTLS와 no redirect/proxy/userinfo/arbitrary header를
  각각 깨뜨린 fixture도 0회다.
- `external_approved`도 exact approval revision을 요구한다. 두 tier 모두 missing/stale/expired/revoked
  approval과 provider call/finalization 사이 approval revoke race에서 next call/pointer swap이 0회다.
- Request/graph/document metadata의 endpoint/provider/config/credential override는 provider
  lookup 또는 network I/O 전에 거부한다.
- Organization manager를 포함한 product actor가 Knowledge/provider management request로 private
  endpoint, CIDR, operation profile, transport 또는 network-isolation revision을 만들거나 수정하려 하면
  schema validation에서 `422` zero-write/zero-audit/zero-network로 닫힌다. Private detector success fixture는
  deployment/change-control 과정에서 미리 주입된 immutable server-owned profile revision만 사용한다.
- Localhost, link-local, cloud metadata, unapproved private/public IP/port, DNS rebinding,
  redirect, userinfo, proxy inheritance와 arbitrary header를 egress guard가 차단한다.
- Local parser path는 network call 0회다. `llamaparse`/external parser는 exact Organization opt-in과
  source-managed document의 source scope 또는 manual document의 KB scope opt-in, Raw Parser Egress
  Approval Revision, parser revision/credential capability/`knowledge.parser.external_approved` profile/
  ADR-0067 public-address guarded transport readiness 중 하나라도 없으면 raw upload 전에
  `knowledge.raw_parser_egress_unavailable`로 닫고 detector/embedding도 0회다. 승인 path도 parser raw
  request/response를 durable sink에 남기지 않고 output을 local normalization/hard baseline에 전달한다.
  현재 미지원 path는 source fetch, credential lookup, external parser SDK와 local parser fallback도 모두
  0회이며 preview와 durable ingestion이 같은 exact safe reason을 보존한다.
- External parser destination이 private, loopback, link-local, cloud metadata, multicast, unspecified이거나
  DNS result 중 하나라도 public-address policy를 벗어나면 raw upload 0회다. Private parser fixture는
  Organization approval이나 mTLS가 있어도 별도 Accepted ADR과 dedicated isolation profile 전에는
  `knowledge.raw_parser_egress_unavailable`로 닫힌다.
- Provider I/O spy는 open SQLAlchemy session/transaction/row lock이 없고 bounded
  connect/read/total timeout, request/response size와 concurrency cap을 받는다.
- Provider raw request/response/request id/exception은 application log, error envelope,
  trace, audit, metric label, job/retry/dead-letter fixture에 나타나지 않는다.
- 같은 input/revision에 서로 다른 두 valid span set을 반환하는 fake provider에서 current
  fence의 redacted candidate+manifest staging commit 하나만 성공한다. Commit 이후 retry는
  provider 0회이고 committed candidate를 재사용하며 response를 union/majority-select하지 않는다.
  Commit 전 crash/takeover 재호출에서는 stale generation response가 zero commit이다.

### Policy, Permission And Management Tests

- `baseline_only`는 organization provider를 호출하지 않고 local baseline/mask 뒤에만
  chunk/embedding을 실행한다.
- `enterprise_detector_required`는 baseline -> exact provider -> local mask 순서를
  지키고 provider 실패를 baseline-only로 downgrade하지 않는다.
- `manual_review_required`는 valid redacted candidate를 review pending에 두고 embedding과
  active pointer swap을 하지 않으며 exact safe reason은
  `knowledge.privacy_review_required`다.
- Provider가 null인 `manual_review_required`도 review pending에 도달하며 Organization manager가
  candidate-relative UTF-8 byte mask range를 제출하면 replacement/raw body 없이 정보가 줄어드는 새 immutable
  candidate revision을 만든다. Local hard baseline을 다시 실행하고 상태는 pending으로 유지하되 최초
  generation의 `retention_expires_at`을 승계하며 provider,
  chunk/embedding과 active pointer swap은 0회다.
- Mask range의 UTF-8 middle boundary, reversed/out-of-range/over-cap/duplicate 입력, replacement text 또는
  전체 candidate/raw body field는 zero-write validation failure다. Valid overlapping ranges는 canonical union 뒤
  deterministic output을 만들고 같은 expected revision의 concurrent mask/approve 중 한 winner만 commit한다.
- Exact current candidate approval은 current effective policy/Collection privacy binding, platform/Organization
  validity, actor/source authorization과 authoritative DB-time expiry를 다시 확인하고 append-only approve decision, generation review-state와
  canonical review audit를 같은 Unit of Work에 commit한다. Embedding은 이 transaction 밖에서 승인된 immutable revision으로 재개하고 finalizer가
  같은 authority/policy/validity/fence를 다시 검증한다. 보지 않은 미래 content/KB blanket approval, stale generation/candidate approval과
  terminal `block` candidate 승인은 zero-write이며 provider를 호출하지 않는다.
- Candidate row는 review command로 overwrite되지 않는다. Mask는 successor candidate/manifest와 append-only
  decision/audit가 모두 commit되거나 모두 rollback되고, approve/reject는 exact candidate decision,
  generation review-state와 audit가 원자적이다. Durable decision/audit에는 submitted range, candidate body,
  raw/span/digest가 없으며 response-loss retry는 같은 expected revision에서 duplicate decision을 만들지 않는다.
- Candidate body는 encrypted protected staging에만 있고 최초 생성 DB time부터
  `privacy_review_candidate_ttl_v1 = 7 * 24 hours`를 적용한다. Successor mask를 반복해도 expiry는
  연장되지 않는다. TTL 직전은 current gate를 통과할 수 있지만 equality부터 list/detail/mask/approve가
  hidden 또는 stale safe failure와 zero mutation/audit로 닫히고 새 privacy attempt가 필요하다.
- Approve decision은 candidate를 review projection/mutation에서 닫지만 exact encrypted body는 TTL 안의
  `approved_pending_finalization` input으로 남는다. Canonical active artifact finalization commit과 같은
  transaction에서만 `purge_pending`으로 바뀐다. Approve commit 뒤 finalizer 실패/재시도와 cleanup worker를
  경합시켜도 finalization 전 body가 삭제되지 않고, TTL equality 또는 stale authority가 이기면 finalization은
  fail-closed하고 새 candidate/review가 필요하다.
- Reject, canonical finalization commit, successor 확정, generation-bound source/ingestion authority revoke와
  stale/abandoned generation은 predecessor/current candidate body를 즉시 non-projectable, non-mutable
  `purge_pending`으로 만든다. 개별 reviewer의 manager/source-display 권한을 회수하면 그 actor의 조회/변경만
  hidden zero-write이고 다른 authorized reviewer가 처리할 candidate는 보존한다.
  Current cleanup claim/fence owner만 bounded batch로 24시간 안에 physical body와 retired staging key를
  삭제하고 receipt/tombstone을 확정한다. Stale worker는 current candidate, append-only Decision, safe audit,
  manifest와 다른 generation body를 삭제하지 않는다.
- Legal hold는 candidate body physical purge만 보류하고 TTL, review projection 또는 approval을 재개하지
  않는다. Hold 해제 뒤 같은 purge generation이 cleanup을 완성하며 terminal Decision/audit/manifest와
  receipt는 candidate body와 분리 보존된다. Protected staging/expiry/cleanup readiness 중 하나라도 없으면
  review-capable policy activation은 zero-write다.
- Mode x provider ref x strongest action의 전체 cross-product를 table-driven으로 검증한다.
  `baseline_only`는 null provider만, `enterprise_detector_required`는 exactly one active same-Organization
  provider만, `manual_review_required`는 null 또는 exactly one provider만 허용한다. Manual mode의
  non-block 결과는 모두 pending이고 terminal block은 review 없이 차단한다. 다른 두 mode의
  `manual_review` action도 pending이며 review readiness가 없으면 policy publish/activate가 zero-write다.
  Non-null provider만 credential/egress-approval readiness를 요구하고 invalid 조합을 provider 제거,
  `baseline_only` 또는 `mask`로 보정하지 않는다.
- Organization base보다 강한 Collection/source/KB override 조합을 table-driven으로 검증한다. 모든
  explicit active Collection Privacy Policy Binding이 포함되고 UUID order를 바꿔도 compiled digest/effective result가 같아야
  한다. Category action은 strongest union, detector/manual-review requirement는 OR이며 하위 scope의
  omission/delete가 상위를 약화하지 않는다. Distinct provider 2개, required detector의 null provider,
  inactive/cross-Organization scope/provider는 external parser/provider/embedding 전 whole-policy failure다.
- Collection privacy binding, source binding 또는 scoped revision이 snapshot 뒤 commit 전에 바뀌면 stale
  finalizer가 zero commit이고, effective scope/result 변경 winner는 해당 Organization validity epoch `+1`과
  canonical management audit를 원자 확정한다. 다른 Organization epoch은 바뀌지 않는다.
- Routing membership link/reorder만 바뀌면 effective privacy scope/digest와 Organization validity epoch은
  바뀌지 않는다. Binding create/replace/delete는 active same-Organization membership, Organization manager,
  bounded impact acknowledgement와 expected membership/binding/policy/current validity revision을 모두 요구하며
  stale/concurrent loser는 binding/epoch/audit zero-write다.
- Collection archive/restore만 바뀌어도 active binding과 effective privacy scope/digest, Organization validity
  epoch은 유지된다. Archived Collection의 binding을 resolver가 누락하거나 delegated lifecycle actor가
  privacy를 약화하면 테스트 실패다. Active binding이 있는 hard delete/policy purge는 cascade 0건의 safe conflict다.
- Pending review list/detail에는 staged redacted candidate, bounded safe outcome과 opaque
  manifest/generation ref만 있고 raw, exact span/confidence, provider view/map과 reversible mapping은
  없다. Source-managed candidate는 list/detail projection 직전 fresh requester source authorization/
  display policy를 통과해야 한다. Gate가 없거나 revoke가 먼저 commit되면 `404 resource.hidden`이고
  candidate/ref/state를 반환하지 않는다. Manual KB fixture는 source gate 없이 manager review를 허용한다.
- Source-managed mask/approve/reject가 candidate를 읽은 뒤 source revoke와 경합하면 commit 직전 recheck의
  revoke winner가 `404 resource.hidden`, zero mutation/audit로 닫힌다. Review role/Organization manager
  지위는 source display gate를 우회하지 않는다.
- Organization manager가 raw/compliance permission, fresh source ACL과 pre-response access audit 없이
  raw endpoint를 호출하면 safe denial이다. 이 raw denial은 redacted review mutation authority와 별개지만,
  source-managed review 자체의 fresh display gate를 제거하지 않는다.
- Current `GET /api/v1/knowledge/{kb_id}/documents/{document_id}/content`를 review/compliance route로
  연결하려는 fixture는 dedicated raw/compliance permission, pre-response audit, retention/legal-hold/
  purge 계약이 없으므로 fail-closed한다. Current route 존재 자체를 target raw surface 증거로 쓰지 않는다.
- Missing/stale policy는 `knowledge.privacy_policy_unavailable`로 닫고 provider, embedding,
  active pointer와 implicit `baseline_only` fallback을 모두 0회로 유지한다.
- Active Organization이 없거나 caller가 KB `write`/Organization manager가 아니면 raw
  fetch/baseline/provider 이전에 safe denial이다.
- Cross-Organization document/policy/provider/review ID는 ownership-first safe hiding으로
  닫고 provider mode/identity/state를 노출하지 않는다.
- Source-managed document는 initial gate와 external batch/finalize 전 fresh source
  authorization/display/raw policy를 요구하며 Organization manager도 우회하지 않는다.
- Background job actor의 membership/KB/source authority 회수 뒤 다른 user, owner 또는
  system actor로 승격하지 않는다.
- Provider/policy lifecycle, credential capability, revoke, exact cap/readiness와 canonical management
  audit가 구성되지 않으면 non-null provider path를 차단한다. Review management/audit가 없으면
  provider ref 유무와 무관하게 `manual_review_required`와 `manual_review` action policy만 차단한다.
  Provider 관리 경계를 충족한 non-review `enterprise_detector_required`까지 review readiness 때문에
  차단하지 않고, 두 readiness가 모두 없을 때만 review action을 만들지 않는 explicit
  `baseline_only` composition으로 제한한다.
- Enforcement migration은 current global platform validity revision/epoch을 만들고 enforcement를 비활성으로
  유지한다. Signup/OAuth/seed/admin writer가 모두 policy/validity dual-write generation으로 수렴하고 구버전
  writer가 drain/fence된 뒤 기존 Organization을 idempotent backfill한다. 최초 scan 직후 구버전 create,
  new-writer create/backfill 경합과 retry를 재현해 모든 Organization이 정확히 한 initial `baseline_only`
  policy/validity set에 수렴하는지 검증한다. Bounded rescan의 missing row가 0이 아니거나 writer-generation
  readiness marker가 stale면 activation/audit zero-write다. Activation은 no-default non-null
  foundation-generation/policy/validity DB constraint와 함께 확정해야 한다. Enforcement 뒤 구버전 writer
  startup/rollback은 readiness failure이고, readiness를 우회한 direct insert도 Organization/foundation partial
  row 0건으로 DB rollback하며 누락 조직을 runtime implicit default로 처리하지 않는다.
- Enforcement 전 생성한 immutable migration inventory/wave만 pre-existing artifact를
  `legacy_unverified`로 표시한다. Request/graph/runtime command와 새 ingestion/reprocess 결과는
  enrollment할 수 없고, artifact는 최대 한 wave에만 속하며 client는 later-wave 이동 또는
  admission deadline/cutoff를 연장할 수 없다. `admission_deadline_at <= retrieval_cutoff_at <=
  enforcement_activated_at + 30 * 24 hours` 위반은 inventory/rollout/audit zero-write다.
- `privacy_legacy_grace_v1`은 exact 30 x 24시간 code-owned maximum이다. Cutoff가 activation+30일과
  같으면 wave를 만들 수 있지만 해당 cutoff 시각부터 retrieval은 닫힌다. 상한보다 최소 representable
  DB interval만큼 큰 값, missing/unsupported grace contract, mutable/missing activation time은 모두
  zero activation/audit다. Deployment setting은 상한을 줄일 수만 있고 Organization별 확장은 거부한다.
- Wave provision은 deployment-owned platform migration principal과 explicit Organization
  allowlist를 요구한다. Inventory, rollout marker와
  `knowledge.privacy_migration_wave.created` canonical audit가 같은 transaction에 한 번
  commit되고 실패/retry는 partial inventory나 duplicate audit를 만들지 않는다.
- Bounded inventory staging 중인 item은 `legacy_unverified` retrieval eligibility가 없다. Final
  freeze는 exact current retrieval-visible raw-derived set 불일치에서 zero activation/audit이고,
  Organization rollout coordination lock 아래 set validation, `legacy_snapshot_revision`,
  enforcement epoch/marker, frozen header와 audit를 원자 commit한다.
- Inventory identity는 active version이 있는 versioned artifact와 `document_version_id IS NULL`인
  legacy unversioned artifact를 각각 exact opaque `legacy_artifact_ref`로 결속한다. Ref는 해당
  artifact의 chunk와 vector/keyword/hierarchy generation 전체를 포함해야 하며 document wildcard,
  일부 chunk/index만의 등록, cross-Organization set 혼합은 zero activation/audit다. Staging 뒤
  exact set의 추가·삭제·교체도 final freeze mismatch로 실패한다.
- Legacy-producing pointer writer와 freeze를 경합시켜 writer winner 뒤 freeze가 inventory를
  재대조하고, freeze winner 뒤 writer가 epoch CAS에 실패해 legacy commit 없이 privacy-gated
  fresh admission하는지 실제 PostgreSQL에서 검증한다.
- Allowlist/wave가 없는 Organization은 legacy retrieval이 unavailable이고 ambient system actor,
  owner/member/client field로 wave를 만들 수 없다.
- Policy/provider management와 review endpoint가 후속으로 생기기 전에는 corresponding
  route가 없고 request에 control field를 넣어도 `422` zero-write다.
- Deployment standalone preflight, active create와 enable/toggle에 current-valid manifest,
  compliant-pointer history, frozen/current legacy validity epoch equality, retirement와 DB-time cutoff를 같은 resolver로 입력한다. Stale/invalid
  manifest, compliant pointer 뒤 물리 legacy가 남은 경우, retired/expired legacy와 artifact 부재는
  preview `200 OK`의 safe `status="blocked"`이고 `is_active=false`에서 inactive 저장이 허용되는
  availability blocker만 기존 Deployment 계약에 따라 `warning`으로 낮춘다. Active mutation은
  `409 deployment.preflight.blocked`로 닫힌다. Hidden document/provider/policy identity와 exact count는
  응답에 없다.

### Ingestion, Failure And Concurrency Tests

- Baseline crash/timeout/unknown은 `knowledge.privacy_baseline_failed`이고 provider,
  chunk, embedding/index와 active pointer call/write가 0회다.
- Baseline terminal block은 `knowledge.sensitive_content_detected`이고 provider/embedding/
  finalization이 0회다. Provider terminal block은 같은 safe reason과 canonical
  `policy.block` reason으로 수렴하고 embedding/finalization이 0회다.
- Terminal block의 safe attempt state와 generic audit Outbox intent는 같은 transaction에서
  한 번 생성된다. Outbox insert/flush/commit 실패를 각각 주입하면 attempt/artifact/pointer가
  0건이고 raw/direct-broker fallback도 없으며 exact retry가 duplicate audit를 만들지 않는다.
- Provider missing/inactive/revoked/timeout은
  `knowledge.detector_provider_unavailable`, malformed/fingerprint/coverage/span/cap은
  `knowledge.detector_response_invalid`이며 둘 다 partial result를 저장하지 않는다.
- Normalized input platform cap 초과는 provider/embedding 전에
  `knowledge.privacy_input_too_large`로 실패한다.
- Same materialization identity의 concurrent worker 둘 중 valid claim generation 하나만
  provider/embedding commit 권한을 얻는다.
- Provider call 중 cancel, policy/provider revoke, actor/KB/source revoke와 fence takeover를
  각각 경합시키고 loser가 progress/outcome/cleanup/finalization을 commit하지 않음을 실제
  PostgreSQL test로 검증한다.
- Gate 전 revoke winner는 next external call 0회, in-flight call 뒤 revoke winner는 다음
  batch/embedding/finalize 0회다.
- Provider/embedding failure, manual review, cancellation, stale generation과 process crash는
  current-valid인 기존 compliant active-ready version과 retrieval result만 유지한다.
- Credential rotation/operational endpoint 변경이 server validator에서 `artifact_preserving`으로
  판정되면 current revision은 전진해도 해당 epoch이 유지되어 기존 manifest가 current-valid로 남고
  frozen legacy wave도 cutoff 안에서 eligible하며 새 admission/finalization만 새 revision을 사용한다.
  Baseline security supersession은 global platform
  epoch을, stronger Organization action/new required detector, provider/egress-approval security
  invalidation과 unknown compatibility는 해당 Organization epoch을 정확히 1 증가시킨다. Current
  revision/epoch CAS와 canonical audit commit 즉시 active pointer 상태와 무관하게 old epoch artifact를
  prefilter/final evidence gate에서 제외한다. Grace 중 legacy wave도 freeze한 platform/Organization epoch와
  current epoch가 달라지는 같은 commit부터 item bulk update/cleanup 없이 runtime, candidate resolver,
  `chunk_count`와 deployment preflight에서 제외하며 비동기 projection 지연과 stale cache/vector hit도
  우회하지 못한다.
- Platform invalidation은 서로 다른 Organization의 old platform epoch manifest를 모두 제외한다.
  Organization invalidation은 target Organization의 provider 사용 여부와 무관한 모든 privacy-gated
  artifact를 보수적으로 제외하지만 다른 Organization epoch/artifact는 바꾸지 않는다. Client가 scope,
  revision 또는 epoch을 주입해 영향 범위를 줄이거나 current로 위조할 수 없다.
- Concurrent invalidating mutation은 expected current revision/epoch CAS winner만 정확히 `+1`과 audit를
  commit한다. Stale loser는 revision, epoch, policy/provider/approval pointer와 audit를 zero-write로
  유지한 뒤 fresh preview/command가 필요하다. Missing, zero/negative, malformed 또는 current pointer와
  불일치한 epoch은 retrieval/readiness/finalization 모두 fail-closed한다.
- Invalidating transition과 새 generation failure를 경합시키면 과거 pointer는 historical reference로
  남을 수 있지만 retrieval은 재개되지 않는다. Validity transition/audit commit 실패는 둘 다
  rollback하고 부분 invalidation 또는 unaudited validity change를 만들지 않는다.
- Explicit pre-cutoff inventory 밖에서 기존 current-valid compliant active-ready version이 없는 동일 실패 행렬은 document를
  retrieval-unavailable로 유지하고 legacy/raw/staging/partial chunk/vector를 반환하지 않는다.
- Inventory에 고정된 pre-cutoff `legacy_unverified` artifact는 reindex 실패 전후 같은 pointer를
  유지하지만 wave freeze 당시 exact platform/Organization validity ref/epoch도 고정한다. Current 두 epoch와
  하나라도 달라지거나 cutoff가 지나고 current-valid compliant active-ready version이 없으면
  retrieval-unavailable로 전환한다. Cutoff 직전/동시 reindex와 cleanup 경합은 current fence/CAS 한
  winner만 허용한다.
- Privacy-compliant active pointer swap은 같은 transaction에서 해당 document의 legacy eligibility를
  retire한다. Pointer commit만 성공하거나 retirement만 성공하는 fault injection은 전체 rollback이다.
  이후 active manifest를 invalidating epoch으로 stale하게 만들고 frozen legacy artifact와 남은 cutoff를
  유지해도 retrieval, candidate resolver, `chunk_count`와 deployment preflight 모두 legacy를 사용하지
  않고 unavailable로 닫힌다. Cleanup 미완료와 pointer historical row 삭제/상태 변경도 eligibility를
  복구하지 않는다.
- Cleanup receipt가 확정된 legacy artifact는 retry, rollback 또는 새 wave에서 재활성화되지
  않으며 inventory revision 변경이나 client 재등록으로 부활하지 않는다.
- Legacy cleanup pre-delete transaction 실패/fence loser는 external delete 0회다. 성공하면 exact
  ref가 먼저 non-retrievable `purging`이 되고, physical absence 확인 뒤 receipt, append-only
  tombstone과 `knowledge.processing_artifact.purged` audit가 같은 completion transaction에 한 번
  commit된다. Completion 실패는 visibility를 복원하지 않고 same-generation reconciler retry가
  duplicate receipt/tombstone/audit 또는 다른 generation 삭제를 만들지 않는다.
- Deadline 전에 admitted된 attempt가 cutoff 뒤 완료되면 fresh actor/source/policy/provider/
  egress-approval, current platform/Organization validity revision/epoch과 fence를 모두 통과한 compliant
  artifact만 활성화하고, 완료 전 legacy retrieval은 재개하지 않는다.
- DB time이 deadline/cutoff 직전, 정확히 같은 시각, 직후인 경계를 검증한다. Equality부터 새
  admission/legacy retrieval이 닫히고 delayed scheduler, skewed application/client clock, stale
  cache/vector hit도 prefilter와 final evidence gate를 우회하지 않는다.
- Redacted canonical, chunk, embedding/index, Privacy Decision Manifest 중 하나라도 없거나 current
  actor/source/provider/egress-approval/policy, platform/Organization validity revision/epoch과 fence가
  아니면 ready/current/active pointer를 바꾸지 않는다.
- Finalization transaction은 canonical/manifest/artifact completeness와 pointer/outbox를
  원자적으로 확정하고 audit failure가 결합된 management/review mutation은 전체 rollback한다.
- Recovery는 orphan staging artifact와 ephemeral raw/view/map을 정리하되 stale worker가 새
  generation artifact를 지우거나 legacy raw-derived artifact를 compliant로 표시하지 않는다.
- Aborted/unfrozen migration staging은 bounded cleanup되며 frozen wave, legacy eligibility,
  cleanup receipt 또는 success audit로 투영되지 않는다.

### Data, Audit And Trace Tests

- Privacy policy/provider/egress-approval/validity revision, attempt와 manifest schema는 exact Organization
  scope, immutable revision, active/expired/revoked lifecycle, closed mode/provider/action precondition과
  `artifact_preserving|artifact_invalidating` effect를 round-trip한다. Validity fixture는 `platform|organization`
  scope, positive monotonic epoch, previous/current revision과 manifest의 exact platform/Organization
  ref+epoch pair를 고정한다. Preserving은 same epoch, invalidating은 exact `+1`만 허용하고 unknown scope,
  missing pair, epoch skip/decrease와 cross-Organization ref를 거부한다.
- Low-entropy raw source가 같은 두 Organization의 durable source/span digest와 audit/trace에서
  cross-tenant equality correlation을 만들지 않는다. Key version은 manifest에만 있고 일반
  response/trace/audit에 없다.
- `privacy_digest_hmac_sha256_v1` golden fixture는 domain label, Organization UUID, key version,
  length-delimited canonical bytes의 순서/경계를 고정한다. 같은 Organization/input/version은
  같은 digest, Organization 또는 key version이 다르면 다른 digest다. Master/derived key가
  DB/wire/job/audit/trace/log에 없고 current key 부재는 provider/embedding 전에 실패한다.
- Key rotation 중 같은 attempt retry/recovery는 snapshotted version과 digest를 유지하고 새
  admission만 current version을 사용한다. Rotation만으로 기존 manifest/active artifact를
  rewrite, reindex, stale 또는 권한 변경하지 않는다. Snapshot key revoke/destruction winner는
  next external batch/finalization을 차단한다. Historical key destruction은 in-flight attempt와
  manifest retention/legal hold, 영향 preview/audit 없이 실행되지 않으며 exact canonical
  action/transaction ownership이 없는 destructive management surface는 disabled다.
- Raw input의 sensitive canary는 redacted canonical/chunk artifact와 모든 raw-free operational
  sink에서 부재한다. Non-sensitive redacted canonical/chunk body는 전용 artifact storage에만
  존재하고 job/retry/dead-letter, API/SSE status, audit/trace/log/metric에는 복사되지 않는다.
  Normalized/view text, exact span/confidence, source/canonical/span digest, endpoint/provider/
  credential/request id와 parser/provider exception도 위 raw-free sink에서 모두 부재한다.
- Internal `legacy_artifact_ref`, nullable-version migration fact, internal wave/item identity와 exact
  chunk/vector/keyword/hierarchy membership은 public API/SSE, audit, trace, log와 metric에서 모두
  부재한다. 단, canonical `knowledge.privacy_migration_wave.created` audit에는 server-issued safe opaque
  wave ref 하나를 허용·요구하고 cleanup completion audit에는 allowlisted opaque wave/receipt/tombstone
  ref만 허용한다. 이 opaque ref가 internal wave/item primary key나 exact membership을 복원할 수 없어야
  하며 public API/SSE/trace/log/metric에는 나타나지 않는다.
- Runtime privacy block은 canonical `policy.block`과 safe `policy_reason`을 사용하고 normal
  successful detection마다 high-cardinality AuditLog를 만들지 않는다.
- Parent/source-hidden failure는 privacy-specific reason, attempt/manifest/provider identity를
  만들거나 반환하지 않는다.
- Protected raw artifact opt-in이 없는 기본 경로는 raw를 durable 저장하지 않는다. Opt-in
  fixture도 raw/compliance permission, fresh source ACL, access audit, retention/legal-hold/purge를
  통과하지 못하면 반환하지 않고 RAG/embedding/prompt 입력으로 사용하지 않는다.
- Current `documents.file_path`와
  `GET /api/v1/knowledge/{kb_id}/documents/{document_id}/content` fixture를 protected raw
  artifact 준수 증거로 재사용하지 않는다. Target cutover는 Nodease 저장소의 exact upload/fetch raw-copy
  inventory를 freeze한다. Valid opt-in과 retention/legal-hold 보존 조건을 모두 충족한 item은 encrypted
  protected store로 이관하고 destination integrity와 old-copy physical absence를 확인한다. 유효한 opt-in이
  없거나 retention 정책이 보존을 허용하지 않아 protected migration 조건을 충족하지 않고 hold가 삭제를 막지 않는 item은
  non-readable fence 뒤 물리 삭제하고 purge receipt와 absence를 확인한다. No-opt-in legal-hold item은 자동
  이관/삭제하지 않고 cutover를 차단한다. 모든 item이 `protected_migrated|purged`로 terminal 수렴하기 전에는
  enforcement를 활성화하지 않으며 source system의 opaque protected reference만 raw body 복사본이 아닌 것으로
  분류한다. All-terminal winner는 Organization별 final readiness marker와
  `knowledge.raw_copy_cutover.completed` Audit Outbox intent를 같은 transaction에서 한 번 확정한다. Audit fault는
  readiness도 rollback하고 retry/reconciliation은 per-item AuditLog를 만들지 않는다.
- Migration/purge 중 crash, unknown disposition, legal-hold conflict, DB path만 제거된 object, migration 뒤
  남은 readable duplicate와 physical-absence 확인 실패를 각각 주입한다. 이 경우 cutover readiness와
  success receipt는 zero-write 또는 incomplete로 남고 재조정 전 activation은 0건이다. Response route를
  fail-closed한 것만으로 storage disposition을 통과하지 못하며 receipt/audit에는 raw body, object key,
  content hash, item/document identity, exact count와 internal inventory identity가 없어야 한다.
- `protected_migrated` item도 dedicated raw/compliance permission, fresh source ACL, pre-response audit와
  retention/legal-hold/purge gate가 없으면 raw body response를 반환하지 않는다. Frozen legacy retrieval
  eligibility는 raw-copy 보존 근거가 아니며 raw-copy purge와 별도 상태로 검증한다.
- 검출용 source bytes를 isolated ephemeral handle로 열어도 attempt/job/retry/dead-letter,
  provider payload snapshot과 crash-recovery record에는 복제하지 않고 종료/취소/worker loss 뒤
  bounded cleanup한다.

### Evaluation And Rollout Tests

- 한국어/영어 일반 text, 표/OCR noise/PDF 줄바꿈, email/phone, 실제 개인과 연결되지 않는
  deterministic synthetic 주민등록번호·계좌/카드·사번 유사값, token/private-key pattern과
  secret-like hard negative를 versioned corpus로 평가한다. Fixture에는 실제 PII/credential을
  넣지 않는다.
- Category/언어/문서 유형별 precision, recall, false-positive/negative, quarantine/review rate,
  latency/cost와 baseline 대비 provider 추가 탐지율을 기록한다. Raw evaluation fixture는
  승인된 test data 경계 밖에 복사하지 않는다.
- Shadow mode는 raw text/span/provider payload를 telemetry에 남기지 않고 enforcement
  decision을 active artifact에 적용하지 않는다.
- New ingestion allowlist, Organization 확대, legacy reindex, cleanup과 rollback 단계를
  분리하고 각 단계에서 기존 active-ready availability와 no-raw-egress canary를 검증한다.
- Legacy raw-derived chunk/vector는 successful privacy reindex와 cleanup receipt 전까지
  compliant label을 받지 않고 rollback target으로 자동 선택되지 않는다.

실제 PostgreSQL, migration upgrade/downgrade, provider fake-server/network와 E2E 실행은 CI에
위임할 수 있지만 위 테스트 코드는 MBA-362에서 작성한다. 로컬은 Shared Privacy Core,
port/adapter/application unit/contract test와 변경 package 정적 검사를 우선한다.

## Phase Acceptance Tests

- Phase 1 acceptance에는 egress negative paths, content safety/parser isolation, protected source identity, basic sync, redaction, active version swap, transactional outbox insert, fencing token, recovery scanner smoke 테스트가 포함된다.
- Phase 2 acceptance에는 source ACL freshness, content cursor와 ACL/permission watermark 분리, Knowledge Permission Helper, KB `use` + source ACL two-gate, source-policy grant inactive lifecycle 테스트가 포함된다.
- Phase 3 acceptance에는 multi-KB caps, final evidence recheck, resource hiding matrix, retry/dead-letter transition, partial result behavior, runtime authorization batch/fallback 테스트가 포함된다.
- Live-linked mode를 구현하는 phase는 requester-scoped/opaque-ref-only side-channel 테스트를 포함한다.
- Source-managed public exposure를 구현하는 phase는 approval scope/target validation과 revocation propagation 테스트를 포함한다.
- Golden questions, source tier tuning, LLM-assisted rewrite, advanced rerank는 core safety phases 이후 별도 acceptance로 확장한다.
