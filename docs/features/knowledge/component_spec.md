# Knowledge Component Spec

Status: Draft
Verified Against: feature/mba-354 @ 6eb2e6d37e139b1f2c299326db336433e394cf30
MBA-105 구현 baseline, 운영 기본값, permission helper output, active version finalization, resource hiding matrix는 [implementation_baseline.md](implementation_baseline.md)를 따른다. Workflow RAG에서 `execution_subject`가 없는 MVP public-only runtime은 [ADR-0018](../../decisions/ADR-0018-workflow-rag-anonymous-public-only-runtime.md)을 따른다. MCP/API source connector와 incremental sync 경계는 [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md)을 따른다. Direct KB와 명시 selected Collection의 Workflow runtime candidate 해석은 [ADR-0036](../../decisions/ADR-0036-knowledge-runtime-candidate-resolution.md)을 따른다. KC lifecycle, item 순서와 권한 운영 경계는 [ADR-0044](../../decisions/ADR-0044-knowledge-collection-operational-management-boundary.md)을 따른다.
KC sync의 Gateway application, durable repository, Workflow executor와 Client polling 경계는 [ADR-0048](../../decisions/ADR-0048-knowledge-collection-sync-execution-boundary.md)을 따른다.
Organization Detector Provider와 embedding 전 local masking Target은 [ADR-0070](../../decisions/ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)을 따른다. 현재 component/runtime 구현 완료를 뜻하지 않는다.

## Domain Components

| Component | 책임 | 경계 |
| --- | --- | --- |
| Knowledge Source Connector | Adapter policy를 통해 source item과 source ACL을 열거하고 가져온다. MCP/API source는 server-side allowlist operation으로만 호출한다 | mbased permission을 직접 결정하지 않고, LLM 임의 tool-use나 raw source direct fetch surface가 아니다 |
| Knowledge outbound operation adapter | API source, remote document fetch와 external preview를 operation별 immutable profile과 Shared guarded transport로 실행한다 | HTTPS/443, bounded same-origin redirect, address-pinned TLS와 safe failure를 강제하지만 SQL/SSH/SaaS 의미, credential 선택과 parser policy를 소유하지 않는다 ([ADR-0067](../../decisions/ADR-0067-production-https-and-operation-bound-outbound.md)) |
| Protocol Adapter | Read-only probe, SQL/command deny, listing cap 같은 protocol-specific safe behavior를 수행한다 | 승인된 guard/client/dialer를 사용해야 한다 |
| Sync Scheduler / Worker | Lease, cursor, retry/backoff, dead-letter, tombstone, sync run state를 관리한다 | Raw source metadata를 노출하지 않고 safe state/reason summary만 낸다 |
| Source Identity Store | Protected/HMAC source identity reference와 tombstone matching을 관리한다 | User-facing document resource가 아니다 |
| Source Subject Mapping Store | Nodease execution subject와 source subject의 mapping state와 epoch를 관리한다 | `unmapped`, `ambiguous`, `stale`, `revoked` 상태는 private retrieval에서 fail-closed이며 raw principal을 user-facing surface에 노출하지 않는다 |
| Source Authorization Provenance Store | Source ACL fact를 requester authorization provenance와 freshness evidence로 materialize한다 | KB `use` grant 자체가 아니며 retrieval permission은 Knowledge Permission Helper가 two-gate로 평가한다 |
| Runtime Source Authorization Client | `check_access_batch` 또는 bounded `check_access` fallback으로 retrieval 실행 시 source 접근을 재확인한다 | Short-lived cache는 optimization일 뿐 권한 원천이 아니며, item/version별 key 없이 subject-level allow를 재사용하지 않는다 |
| Public Exposure Policy Store | Source-managed KB의 anonymous public-only 노출 승인, 만료, 회수, 재검증 상태를 관리한다 | Collection visibility flag만으로 source-managed KB를 public candidate로 만들지 않는다 |
| Content Safety Scanner | Source artifact의 file type allowlist, active content, archive cap, malware/content scan 결과를 평가한다 | Scan pass는 source ACL, KB permission, redaction, prompt-injection guard를 대체하지 않는다 |
| Parser Isolation Worker | PDF/Office/HTML/archive 같은 rich content를 least-privilege 또는 sandboxed 환경에서 text로 추출한다 | Macro, script, embedded object, executable payload, external reference를 실행하지 않는다 |
| LlamaParse Credential Resolver | User-initiated document parsing 직전에 execution subject, active organization, provider compatibility, valid 상태와 credential `use`를 확인하고 단일 허용 parser input을 반환한다 | 이 결과는 raw parser egress 승인이 아니다. FileProcessor가 credential ORM/config를 직접 읽거나 전역 최신 credential, environment fallback, 다른 organization credential을 사용하지 않는다 |
| Privacy/Redaction Service | 순수 PII/secret hard baseline, privacy text/span contract, span 검증/union과 deterministic local masking을 제공한다 | Trace storage, provider I/O, Knowledge lifecycle, DB와 framework를 소유하지 않는다 |
| Collection Privacy Policy Binding Application | Routing membership과 별개인 explicit Collection-to-KB privacy binding의 impact preview, expected-revision CAS, Organization validity epoch와 audit 원자성을 조율한다 | `catalog_manage` membership mutation을 privacy authority로 사용하거나 active binding을 unlink와 함께 암묵 삭제하지 않는다 |
| Effective Privacy Policy Resolver | Platform hard baseline, current Organization base와 모든 explicitly bound Collection/source/KB stricter revision을 strongest union으로 합성하고 exact scope/binding revision, compiled digest와 derived mode/action/provider를 snapshot한다 | Routing membership/order, scope order/last-writer-wins, 하위 scope 약화, client/request/graph/document metadata로 scope/provider 선택 |
| Raw Parser Egress Approval Resolver / Adapter | Explicit Organization과 applicable source-managed source 또는 manual KB opt-in, exact raw-parser approval, parser revision, credential capability, `knowledge.parser.external_approved` profile과 ADR-0067 public-address guarded transport readiness를 확인한 뒤 bounded raw bytes를 승인된 public HTTPS parser에 전송하고 untrusted parsed output을 local baseline으로 반환한다 | Detector approval 재사용, private destination, current LlamaParse credential `use`만으로 upload, raw payload/response durable 저장, baseline 우회 |
| Detector Egress Approval Resolver | Exact provider revision과 same-Organization immutable trust-tier approval의 purpose, processor/ownership·endpoint/network boundary, residency/retention/no-training/no-secondary-use와 active/expiry/revoke를 평가한다 | Private IP, DNS, mTLS 또는 credential 존재를 egress 승인으로 간주하지 않는다 |
| Detector Provider Port/Adapter | Exact effective policy의 same-Organization provider를 tier-specific operation profile로 bounded call하고 실제 전송 bytes와 immutable local call context에 result를 결속한 뒤 provider별 offset/error를 normalized document UTF-8 byte span contract로 변환한다 | Public-address guard를 private용으로 완화하거나 provider echo만을 무결성 근거로 신뢰하거나 baseline span을 제거하거나 provider output text를 canonical로 저장하거나 active pointer를 변경하지 않는다 |
| Provider-Safe View Builder | `external_approved` provider에서 baseline span을 제거/치환한 view와 ephemeral document offset map을 만든다 | Raw document fingerprint, Organization/internal revision, source identity/ACL/credential을 wire payload 또는 durable storage에 넣지 않는다 |
| Privacy Decision Manifest Store | Raw text/exact span 없이 effective scoped policy snapshot/digest, provider/detector-approval/raw-parser refs와 exact platform/Organization validity revision/epoch snapshot, keyed digest, coverage/outcome, redacted candidate ref와 exact candidate revision을 보존한다 | Unapproved candidate manifest를 retrieval authority로 사용하거나 audit/trace에 digest를 복사하거나 manifest를 raw 복구 자료로 사용하지 않는다 |
| Privacy Artifact Validity Resolver | Manifest의 exact global platform 및 same-Organization validity revision/monotonic `validity_epoch` snapshot을 current 두 epoch와 대조하고 preserving/invalidating transition을 retrieval prefilter와 final evidence gate에 동일하게 적용한다 | Active pointer, ready state, cache/vector hit를 current privacy 준수 capability로 취급하지 않는다 |
| Privacy Digest Key Ring | Protected master key version에서 Organization별 domain-separated key를 파생해 `privacy_digest_hmac_sha256_v1`을 제공한다 | Provider/LLM credential을 재사용하거나 key material을 DB/wire/job/audit/trace/log에 전달하지 않는다 |
| Canonical Normalizer | Content safety를 통과한 source text에 hard baseline, required provider, local masking을 순서대로 적용해 staged redacted candidate/metadata를 만들고 approved exact revision만 canonical input으로 승격한다 | Review pending 또는 valid complete privacy result 전에 chunk/embedding을 생성하지 않는다 |
| Privacy Review Application | Exact pending candidate의 range-only 추가 masking, local baseline 재검사, append-only review decision, exact-revision approve/reject와 actor/source/policy/validity fence를 조율한다 | candidate overwrite, submitted range durable 저장, 미래 content blanket approval, replacement text/raw payload 수신, terminal block override, provider 강제 |
| Document Type Registry | Platform-supported document structure capability와 immutable published version을 제공한다 | Organization free-form label 또는 AI output을 runtime capability로 해석하지 않는다 |
| Organization Taxonomy Registry | Draft/validate/publish, versioned sibling-label normalizer, depth-8 replacement graph validation, nullable first-current CAS, transaction-bound organization-lifetime stable topic identity/tombstone, rename/move/deprecate와 impact projection을 관리한다 | Client comparison key를 신뢰하거나 published version을 수정하거나 ledger write를 pointer/audit와 분리하거나 tombstoned ID를 active/다른 의미로 재사용하거나 replacement hint로 assignment를 자동 변경하거나 topic을 Collection, permission 또는 chunk hierarchy로 사용하지 않는다 |
| Classification Policy Publish Coordinator | Taxonomy/profile-policy publish가 registry 및 Organization/Platform profile catalog compatibility snapshot을 commit 전에 재검증하게 한다. 전역 lock order는 applicable Organization catalog, Platform catalog, Organization coordination, taxonomy current, profile-policy current다 | 각 registry가 자기 nullable pointer/catalog revision만 CAS하거나 뒤 단계 row를 잡은 뒤 catalog row를 역순 취득해 first-publish/platform deprecate write skew 또는 deadlock을 만들지 않는다 |
| Taxonomy Impact Preview Application | Exact draft/current compatibility와 assignment impact snapshot을 bounded projection 및 actor-bound opaque revision으로 만들고 publish acknowledgement를 검증한다 | Preview를 선택적 UI read로 취급하거나 hidden identity/exact denied count를 token/audit에 넣거나 stale token으로 publish하지 않는다 |
| Taxonomy Impact Snapshot Port | Assignment/canonical-content/KB-document lifecycle, profile override, current Processing Decision 및 active artifact pointer/availability mutation Unit of Work에서 Organization impact revision을 원자적으로 전진시키고 preview/publish가 exact revision을 읽게 한다 | Async projection, eventual counter 또는 Client bucket으로 publish freshness를 판정하거나 staging-only/no-op 변경에서 revision churn을 만들지 않는다 |
| Classification Assignment Application | Current permission, source-managed protected-row lookup 전 fresh source/display gate와 mutation commit 직전 bounded revision/current KB authority 재검증, nullable assignment absence/revision CAS, 최대 32개 topic, complete effective set과 non-empty `manual_axes`, axis별 authority/lock/content freshness, registry/taxonomy version을 검증하고 selected unlocked axis만 replace/takeover하며 unselected value/source/lock을 보존한다. Taxonomy-less type-only assignment, server-derived `manual_assignment|manual_takeover`, server-computed changed-dimension set과 exact validation reason, content-confirming validation에만 추가되는 `content_read`/raw policy, selected-axis suggestion review, accepted-suggestion safe provenance snapshot, transaction-bound audit와 reindex intent를 조율한다 | HTTP schema, ORM query와 classifier provider를 domain policy에 넣지 않고 sentinel first revision, read-then-insert, duplicate 제거를 통한 topic limit 우회, Client partial topic merge, manual request의 reason/free-text, locked 반대 axis overwrite, stale unlocked axis의 resolver 사용, Client reason으로 changed dimension을 숨기기, content access 없이 content confirmation, value equality만으로 selected manual takeover no-op 또는 suggestion에 의한 manual axis 강등을 허용하지 않는다 |
| Suggestion Accept Preview Application | Fresh parent/KB/source gate 뒤 candidate와 selected axes를 current assignment/resolver/materialization vector에 hypothetically 적용해 bounded effect projection과 actor-bound opaque revision을 만든다 | Client 계산 impact, hidden identity/exact count, raw content/internal fingerprint를 반환하거나 preview token을 capability로 사용하거나 stale token으로 accept하지 않는다 |
| Classification Suggestion Adapter | Exact canonical content revision/hash, nullable current taxonomy와 allowlisted type/topic axis 후보로 최대 32개 topic의 deterministic/AI suggestion을 만든다. Provenance는 공통 immutable `generator_contract_ref`와 `generator_kind=deterministic_rule|ai_classifier` tagged union이며 deterministic kind는 approved rule-set/version, AI kind는 classifier policy/model/prompt-template/calibration 및 bounded confidence만 가진다. Query adapter는 fresh source authorization 뒤 state-bound bounded keyset page만 투영한다 | Oversized adapter output을 durable suggestion으로 저장하거나 taxonomy가 없을 때 topic candidate를 만들거나 generator kind와 맞지 않는 field 또는 deterministic candidate용 fake provider ref를 저장하거나 cursor/저장된 source ref를 capability로 사용하거나 assignment, security classification, permission, profile activation을 직접 변경하거나 profile-only output version으로 suggestion을 expire시키거나 raw prompt/completion/rationale를 저장하지 않는다 |
| Processing Profile Schema Validator | `processing_profile_schema_v1` strict integer size/overlap envelope과 current tokenizer/parser/representation/embedding capability를 publish 전에 검증한다 | Boolean/string/float coercion, legacy character 숫자 재해석, catalog capability로 V1 bound 확대 또는 품질 default 승인을 수행하지 않는다 |
| Profile Mapping Policy Authoring Application | `processing_profile_policy_v1` complete document, raw 2,000-rule cap, matcher union, general fallback scope와 draft/current/catalog CAS를 검증한다 | Omitted rule merge, partial rule operation, duplicate specificity, cross-scope reference 또는 null Platform general publish를 허용하지 않는다 |
| Organization Processing Profile Catalog | Exact empty-object create로 stable identity와 null config/base의 incomplete first-draft shell을 원자 생성하고, complete config PATCH, exact same-identity published `base_revision_id`를 가진 successor draft lineage, draft CAS, immutable publish, `organization_profile_catalog_revision`과 deprecate selectability lifecycle을 관리한다 | First-create field/config를 암묵 수용하거나 incomplete draft를 validate/publish하거나 draft create로 selectability catalog revision을 전진시키거나 published config를 수정/hard-delete하거나 current policy/override reference를 둔 채 deprecate하거나 Organization API로 platform profile을 변경하지 않는다 |
| Platform Processing Profile Catalog Port | Approved platform profile option, non-null exact `platform_default_profile_revision`과 `platform_profile_catalog_revision`을 Organization application에 read-only로 제공한다. Platform registry control plane에는 owner/system actor, required expected catalog revision, target selectability와 canonical `knowledge.processing_profile_default.changed` audit를 검증하는 write port를 제공하고 pointer/catalog/audit를 같은 Unit of Work와 serialization boundary에서 확정한다 | Organization application이 platform lifecycle/default를 변경하거나 uninitialized/mutable latest를 default로 사용하거나 stale/audit-failed pointer mutation을 commit하거나 stale platform revision으로 policy를 publish하거나 current reference/default가 있는데 platform profile을 deprecate하게 하지 않는다 |
| Processing Profile Resolver | Valid explicit override를 먼저 평가한다. Current policy가 있으면 resolver-eligible assignment axis와 immutable policy rule/Organization general/required policy Platform general에서 exact revision을 결정하고, policy가 null이면 catalog의 exact Platform default를 `platform_default` source로 선택한다 | Stale unlocked axis, DB, provider, reindex, AI confidence나 client-supplied profile을 authority로 사용하지 않는다. Platform default를 existing policy fallback과 혼합하거나 invalid explicit override를 조용히 fallback하거나 unavailable default를 mutable latest/env/legacy 값으로 보정하지 않는다 |
| Processing Profile Override Application | KB `manage` 또는 same-organization Organization manager override, source-managed protected-row lookup 전 fresh source/display gate와 set/clear commit 직전 bounded revision/current KB authority 재검증, strict scoped `profile_revision_ref`와 selectable profile, required nullable assignment/profile-policy version, override/default/catalog revision 및 opaque full resolver revision을 검증하고 set/clear, canonical audit와 `reindex_required` projection을 한 transaction에서 확정한다. Default-required branch가 unavailable이어도 selectable override set/change는 recovery로 허용하고 clear는 fixed 503으로 닫는다 | Bare revision ID나 scope를 잃은 reference를 수용하거나 opaque token으로 명시적 nullable pointer precondition을 대체하거나 resolver vector 일부만 읽어 stale projection을 commit하거나 부재를 sentinel로 표현하거나 reindex를 자동 시작하거나 active pointer를 바꾸지 않으며 invalid current override의 clear recovery를 target profile lookup으로 막지 않는다 |
| Knowledge Processing Embedding Binding Port | LLM Credentials domain에 exact profile model/provider, Organization과 immutable job execution actor를 전달하고 active credential, verified relation과 current `use` 후보가 정확히 하나일 때 safe refs 및 lifecycle/relation/permission/provider-routing revision binding을 발급받는다. Fresh job commit 직전에는 같은 authoritative revisions의 serializable revalidation을 요청한다 | Credential value/config를 profile/job payload에 넣거나 0/복수 후보를 name/order/priority/owner/default로 선택하거나 발급 결과를 commit-time authorization capability로 재사용하거나 `job_reused`에서 actor/binding을 교체하지 않는다 |
| Processing Decision Manifest Builder | Canonical content ref, exact resolver input vector/profile과 resolver/materialization input fingerprints를 immutable decision manifest로 고정한다 | Output DocumentVersion ID/result bytes, mutable latest/display 값을 pre-build fingerprint 입력으로 저장하거나 legacy character 값을 token으로 재해석하지 않는다 |
| Artifact Build Manifest Finalizer | `job_created` receipt에 불변 bind된 execution actor의 current permission/source authorization revision과 embedding credential lifecycle/relation/permission binding revision, ready artifact의 immutable build refs, post-build integrity hash와 decision satisfaction을 검증하고 current decision/artifact pointer를 CAS한다. Stale/cancelled attempt는 valid fence에서 terminal state와 cleanup outbox intent만 확정한다 | Cross-actor/credential reuse로 execution actor나 binding을 바꾸거나 revoked actor/credential 또는 stale resolver vector로 satisfaction/active pointer를 commit하거나 staging artifact를 직접 삭제하거나 output integrity hash를 job identity로 사용하지 않는다 |
| Retrieval Representation Builder | Redacted canonical evidence에서 contextual/parent/late/visual retrieval artifact를 deterministic하게 파생한다 | Canonical evidence와 citation identity를 덮어쓰지 않는다 |
| Raw Artifact Store | Nodease가 보존하는 upload/fetch 원문을 opt-in compliance view용 protected raw artifact로 암호화 저장한다. Source system의 source-of-record object를 가리키는 opaque protected reference와 raw copy를 구분한다 | Current `file_path`를 Target 준수 증거로 간주하지 않고 RAG, embedding, prompt, router input, Agent answer stream에서 사용하지 않는다 |
| Raw Copy Cutover Coordinator | 기존 Nodease-held upload/fetch raw copy를 exact inventory로 freeze하고 valid opt-in과 retention/legal-hold 보존 조건을 모두 충족한 item의 protected migration, migration 조건 미충족·no-hold item의 non-readable fence+physical purge를 original-copy absence와 terminal receipt로 수렴시킨 뒤 final readiness와 canonical completion audit을 원자 확정한다 | Legal hold만으로 opt-in을 만들거나 raw response 차단, DB path null 처리, no-opt-in hold, unknown/partial disposition, readable duplicate 또는 audit 실패를 cutover 완료로 간주하지 않는다 |
| Ingestion Concurrency Guard | Same source item/document-level KB 처리의 owner-token lock, fencing token, advisory lock을 제공한다 | Lock TTL 만료 뒤 stale worker가 새 artifact를 finalize하거나 lock을 해제하지 못하게 한다 |
| Ingestion Pipeline | Document version, chunk, embedding artifact, external index entry를 생성한다 | 성공 전 active version을 바꾸지 않는다 |
| Document Ingestion Application | Process/sync/resume/reindex admission, same-document single-flight, duplicate resume 재사용 우선 판정, optional Connection reference UoW, protected input revision과 atomic Document/job transaction을 조율한다 | FastAPI/Celery/SQLAlchemy concrete API를 domain policy에 넣지 않고 raw source config를 job payload로 만들거나 active 동일 intent를 변경된 Document status만으로 거부하지 않는다 |
| Document Ingestion PostgreSQL Adapter | Organization-scoped row lock, active-job unique, PostgreSQL actual wall-clock execution/dispatch lease, heartbeat/fencing, canonical `KnowledgeBase -> Document -> job` claim/success/failure/recovery lock과 recovery scope `SKIP LOCKED`, retry/dead-letter/redrive와 retention query를 구현한다 | Celery task ID, transaction-start `now()`나 Redis 상태를 실행 권위로 사용하거나 잠긴 첫 recovery 후보를 기다리지 않는다 |
| Knowledge Ingestion Worker | Gateway image의 parser/storage dependency로 `knowledge` queue만 소비하고 immutable job execution actor 및 embedding binding의 claim/각 external batch/finalization fresh authorization 뒤 job runner를 실행한다. Authorization session은 I/O 전에 닫고 processor reason normalization, migration/keyring startup gate와 LOCAL shared upload volume을 적용한다. Provider-neutral Helm production reference에서는 bounded replica/concurrency로 활성화되고 recovery Beat가 필수이며 exact local Celery self-ping이 readiness를 판정한다 | Reusing actor/credential로 job authority나 binding을 교체하거나 receipt/job/fence를 capability로 사용하거나 Workflow node queue를 소비하거나 source/credential config를 task argument로 받지 않고 raw processor error로 retry를 판정하지 않는다. 다른 replica의 pong을 local readiness로 인정하거나 broker 장애에 liveness restart loop를 만들지 않는다 |
| Document Ingestion Progress Projection | Redis의 document progress key를 non-authoritative SSE 가속 projection으로 관리하고 current job lease 확인 뒤 chunk 준비는 99 이하, 성공 commit 뒤 완료는 100으로 발행하며 새 admission과 retry/cancel/dead-letter/recovery commit 뒤 이전 attempt key를 삭제한다 | DB transaction을 소유하거나 lease 미확인/pre-finalization progress를 발행하거나 lease를 잃은 worker가 새 attempt cache를 삭제하지 않는다 |
| Document Ingestion Status Projector | Internal job row를 operation/status/attempt/retryability/safe reason/timestamp allowlist로 축소한다 | owner/fencing token, input revision, idempotency key, raw error/source/provider payload를 반환하지 않는다 |
| Active Version Finalizer | Transactional active version pointer swap, previous version `superseded` 표시, content_hash/fingerprint commit, outbox insert를 수행한다 | Fencing/recovery gate가 필요하며 hash만 먼저 commit하거나 pointer swap 후 outbox insert 전에 crash window를 만들지 않는다 |
| Artifact Cleanup Reconciler | DB state와 object storage/vector index/external artifact cleanup을 outbox 기반으로 맞추고 retention pin, purge generation과 append-only Artifact Availability Tombstone을 수렴시킨다 | Manifest/Satisfaction provenance를 영구 pin으로 간주하거나 active/citation/legal-hold pin을 무시하거나 `purging|purged` artifact를 재사용하지 않는다 |
| Privacy Review Candidate Cleanup Reconciler | 최초 generation DB-time expiry, reject/superseded/canonical-finalized/generation-authority-revoked/stale state와 current cleanup claim/fence를 확인하고 encrypted staged candidate body를 bounded batch로 purge한 뒤 receipt/tombstone을 확정한다 | Approve 직후 finalization input을 먼저 삭제하거나 개별 reviewer revoke를 candidate purge로 해석하거나 successor로 TTL을 연장하거나 legal hold를 review 재개로 해석하거나 stale worker가 current candidate/decision/audit/manifest를 삭제하지 않는다 |
| Knowledge Permission Helper | Collection `read`, collection `route`, KB use, source ACL freshness/requester authorization을 bulk 평가한다 | Router와 controller는 permission row가 아니라 helper 결과를 소비해야 한다 |
| Knowledge Document Registration Service | 빈 active manual KB를 잠그고 최초 Document 하나만 원자적으로 등록한다 | HTTP, raw header와 Client 상태를 import하지 않으며 기존 Document가 있으면 상태와 무관하게 typed conflict를 반환한다 |
| Knowledge Document Lifecycle Service | Ingestion과 같은 document advisory lock 순서로 삭제를 직렬화하고 유일 Document 삭제 시 active version pointer와 slot을 함께 정리한다 | Storage physical cleanup보다 DB lifecycle commit을 먼저 확정하고 legacy sibling의 다른 active version을 임의로 해제하지 않는다 |
| Connection Lifecycle Service | Owner Connection row를 잠그고 Knowledge document reference가 없는 연결만 삭제한다 | Client 보상 요청을 신뢰해 참조 중 credential을 삭제하지 않으며 raw connection config를 응답·로그에 노출하지 않는다 |
| Connection Reference Lifecycle UoW | Reference 저장·교체·삭제의 owner Connection row lock, local 2초 wait와 busy/unavailable 분류를 제공한다 | `Connection -> KnowledgeBase -> Document/Version` 역순 lock이나 외부 network/storage/provider 호출을 transaction 안에서 수행하지 않는다 |
| Knowledge Administration Application | Organization manager의 domain grant/revoke, active subject grant validation, stale grant permission-row revoke와 transaction-bound audit를 조율한다 | Grant subject lock과 revoke permission-row lock을 분리하고 controller가 subject 활성 상태를 추정하지 않는다 |
| Workflow Runtime Knowledge Candidate Resolver | Direct KB와 명시 selected Collection을 current authenticated/anonymous audience, lifecycle/readiness, route/use/source gate로 해석하고 direct-first/Collection-round-robin 20-KB set을 만든다 | Shared pure policy + Workflow Engine application/port + PostgreSQL snapshot adapter다. Gateway Builder resolver를 import하지 않고 retrieval/provider를 호출하지 않는다 |
| Knowledge Collection Management Service | Manual Collection CRUD, item link/unlink, visibility, bounded subject page와 single/bundle/bulk permission mutation을 조율한다 | Collection-first lock, last-manage, self-escalation, all-or-nothing을 service가 판단하고 Collection 권한과 KB content 권한을 분리한다 |
| Knowledge Collection Administration Application | Restore와 exact reorder의 authorization·lock·audit·transaction 순서를 port 경계로 조율한다 | Endpoint나 Client가 lifecycle, order revision 또는 persistence policy를 판단하지 않는다 |
| Collection Management PostgreSQL Adapter | Lifecycle/order용 organization-scoped row projection, Collection-first `FOR UPDATE`와 membership lock을 제공한다 | Raw principal을 projection하지 않고 repository port 밖으로 ORM entity를 전달하지 않는다 |
| Collection Management Audit Adapter | 변경된 Collection마다 allowlisted canonical data-change audit를 같은 transaction에 추가한다 | Raw subject/Collection label, request payload, hidden target list와 exact count를 저장하지 않는다 |
| Knowledge Collection Sync Target Scanner | Gateway management projection, sync request와 Workflow worker가 같은 child-source eligibility와 ordered membership snapshot을 계산한다 | DB document를 가진 legacy multi-document KB, API/source-managed child를 fail-closed하고 organization predicate를 모든 query에 적용한다 |
| Knowledge Collection Sync Request Application | Current actor 권한, Collection/source eligibility, idempotency와 single-flight를 검증하고 job/target snapshot/audit를 원자 저장한다 | Gateway application/port/adapter 경계다. Commit 뒤 job UUID만 Celery에 발행하고 source config를 task payload로 만들지 않는다 |
| Knowledge Collection Sync Worker Application | Job lease, fresh worker-start authorization, claim/finalize target-set 재검증, deterministic DB target batch, retry/partial/terminal 집계와 recovery를 조율한다 | Workflow Engine application/port/adapter 경계다. Celery redelivery가 아니라 PostgreSQL job/item 상태가 execution source of truth이며 item attempt는 outcome commit에서 한 번만 증가한다 |
| Knowledge Collection Sync Document Adapter | Shared document advisory lock 뒤 per-target revision을 재검사하고 DB source를 읽어 version-scoped chunk와 active version swap을 같은 UoW에 둔다 | Live target 변경, empty/failure 또는 stale writer가 이전 active ready version을 삭제하거나 `source_deleted` state를 되살리지 못한다 |
| Connection Use Resolver | Opaque Connection UUID와 execution subject user UUID를 한 query의 ID/owner predicate로 평가하고 dial 시작 시점의 권한 스냅샷을 제공한다 | Knowledge upload/process/preview와 Gateway/Workflow Engine processor가 공유한다. Runtime row lock을 소유하지 않으며 KB/Collection/organization 권한을 대체하거나 organization-scoped Connection 정책을 추측하지 않는다 |
| Connection Runtime Snapshot Provider | 독립된 짧은 session에서 Connection Use Resolver를 호출하고 adapter type과 최소 credential configuration을 immutable DTO로 투영·복호화한 뒤 session을 닫는다 | ORM/encrypted storage shape를 processor에 반환하거나 connector/network I/O를 호출하지 않는다 |
| Knowledge Collection Sync Status Projector | Internal job/item 상태를 safe status/progress/reason/timestamp로 축소한다 | Exact child count와 KB/document/source identity, raw exception/config를 default-deny한다 |
| Knowledge Document Response Projector | 내부 `documents.meta_info`에서 safe operational field만 allowlist projection한다 | Encrypted config, connection/source identifier, DB/source config와 unknown nested field를 API response로 전달하지 않는다 |
| Knowledge RAG Recommendation Adapter | `StructuredRequest` 기반 safe intent summary, node purpose summary, knowledge requirement, pending resolution reference를 받아 safe KB recommendation과 LLM node RAG option 후보를 만든다 | Raw natural language 전체를 받지 않고 권한 판단을 직접 하지 않는다. HTTP/serialized boundary에서는 `KnowledgeCandidateResolver`가 만든 server-issued reference만 사용하고, full safe candidate set 객체는 같은 backend 내부 service call에서만 ranking input으로 사용할 수 있다. 초기 구현은 `candidate_type=knowledge_base`만 반환하고 Collection은 safe summary metadata로만 제공한다 |
| Knowledge Skill Registry | Provider-neutral Knowledge Skill, version, owner/review state, freshness/eval status를 관리한다 | Skill은 빌더 단계 LLM node의 RAG 옵션 후보이며 권한 source나 source of truth가 아니다 |
| Source-of-Truth Catalog | 정책 문서, ADR/decision record, semantic definition, curated query corpus 같은 source tier와 safe reference를 관리한다 | Raw content나 hidden source identity를 router에 노출하지 않는다 |
| Skill Context Loader | 후속 target component로, 선택된 skill의 safe metadata와 workflow 생성 요청을 기반으로 필요한 skill body/checklist를 gate 통과 후 점진적으로 로드한다 | MBA-145 Agent Builder MVP에서는 Knowledge Skill body/checklist를 prompt context로 직접 로드하지 않는다. 전역 metadata 선노출과 raw skill resource 로드를 금지한다. 실행 시점 evidence는 별도 authorized retrieval로 가져온다 |
| Skill Evaluation/Regression Set | Golden question, eval result, freshness signal을 관리한다 | Eval fixture도 raw restricted content를 포함하지 않는다 |
| Skill Governance/Publication | Skill publish, review, deprecate, approval workflow의 policy boundary 후보 | 구체적인 authoring UI, Workflow Playground 연결, 승인 UX는 아직 확정하지 않는다. Code-bearing skill은 별도 sandbox/approval gate 전까지 publish할 수 없다 |
| Collection Router | Authorized safe candidate에서 collection/KB 후보를 선택한다 | Access control을 수행하지 않고 raw source ACL이나 hidden aggregate data를 받지 않는다 |
| Retrieval Embedding Model Projection | 권한, organization, lifecycle을 통과한 KB의 distinct embedding model identifier를 한 번의 bounded query로 immutable scalar binding에 투영한다 | Invocation-local 최적화이며 permission, candidate, credential 결정을 소유하지 않는다. Missing, inactive, non-embedding, ambiguous identifier는 provider 호출 전에 fail-closed한다 |
| Query Embedding Execution Port | Authorized 후보의 distinct immutable embedding model binding을 LLM Credentials의 `query_embedding` capability와 single-use provider lease로 실행한다 | Credential policy·secret·provider SDK를 Knowledge 또는 LLM node에 노출하지 않고 ADR-0067 egress와 ADR-0069 durable operation 경계가 없으면 outbound 전에 fail-closed한다 |
| Retrieval Orchestrator | 선택된 KB들에 대해 metadata/hierarchy retrieval을 실행하고 merge/rerank한다 | Authorized redacted evidence만 사용한다 |
| Audit/Trace Summarizer | Redaction-safe audit/trace/answer summary를 만든다 | Raw content/title/path/url은 제외하고, raw/compliance audit은 safe reference, decision, reason만 저장한다 |
| RAG Answer Retention Worker | Terminal answer run의 retention purge를 수행하고 aggregate audit을 남긴다 | requested/running row를 삭제하지 않고 동시 purge를 row lock/marker로 방지한다 |

### MBA-333 Privacy Detection And Pre-Embedding Masking Boundary

이 boundary는 [ADR-0070](../../decisions/ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)의
Target 구조이며 현재 package, table, provider endpoint 또는 worker가 구현됐다는 뜻은 아니다.
Runtime/persistence/provider adapter와 아래 Target 테스트는 MBA-362가 구현한다.

| Layer | 책임 | 금지 |
| --- | --- | --- |
| Shared domain | Unicode 14.0 NFC/LF text contract, UTF-8 byte span 검증/union, hard baseline과 deterministic mask | SQLAlchemy/FastAPI/provider SDK/trace policy import |
| Knowledge application | Actor/source/content-safety gate, effective scoped policy/provider/raw-parser snapshot, raw handle 수명, attempt/fence, canonicalization과 finalization | Raw payload를 Celery/job에 저장, scope/provider failure downgrade |
| Outbound adapter | External detector의 ADR-0067 public profile, private detector의 dedicated isolated profile, protected raw parser profile과 result normalization | Public guard 완화, generic private dial, open DB session/lock 중 I/O, endpoint/CIDR/credential override, raw exception 반환 |
| Persistence adapter | Immutable scoped policy/Collection privacy binding/provider/detector-approval/raw-parser/validity revision, encrypted candidate ref+generation-bound expiry+legal-hold/purge state, append-only review decision/attempt/manifest의 exact platform/Organization epoch snapshot과 current generation/cleanup CAS | Candidate overwrite나 TTL 연장, submitted mask range, raw/view/map/span/parser/provider response 또는 digest key material 저장, stale fence commit |
| Privacy Migration Coordinator | Deployment-owned migration principal의 explicit Organization allowlist, non-authoritative bounded staging, Organization rollout coordination lock의 exact-set freeze/epoch activation, Organization/KB/document/nullable-version과 exact opaque `legacy_artifact_ref`, frozen platform/Organization validity ref+epoch를 가진 immutable one-wave inventory, admission deadline/retrieval cutoff, compliant pointer swap과 eligibility retirement, one-way cleanup 상태 조율 | Document wildcard나 partial index generation을 inventory로 사용, unfrozen/stale-validity staging을 eligibility로 사용, epoch CAS 없는 legacy writer, request/runtime enrollment, later-wave 이동, 새 artifact 추가, client cutoff 연장, compliant pointer 뒤 legacy 복귀, cleanup receipt 뒤 legacy rollback |
| Composition root | Concrete privacy core, policy/manifest repository, Privacy Digest Key Ring, egress guard와 enabled adapter 조립 | Application/domain package에서 concrete adapter import, missing key/readiness provider enable |

`baseline_only`, `enterprise_detector_required`, `manual_review_required`는 서로 다른
explicit policy mode다. `baseline_only` provider ref는 null, `enterprise_detector_required`는
exactly one same-Organization active provider ref, `manual_review_required`는 null 또는 exactly one
provider ref만 허용한다. Manual mode는 terminal block이 아닌 모든 결과를 review pending에 두며,
다른 mode에서도 effective action policy의 strongest action이 `manual_review`이면 같은 상태로
중단한다. Review-capable mode/action은 canonical review management/audit readiness, non-null provider는
provider lifecycle/credential/revocation/egress-approval/exact cap readiness를 요구한다. Missing
readiness에서 composition은 해당 policy를 만들지 않고 provider ref 제거나 action downgrade도 하지
않는다. Background job은 admission actor를 교체하지 않고 각 external batch/finalize 전 current
membership, KB/source/effective-policy/parser/provider/approval, global platform 및 same-Organization validity
revision/epoch과 fence를 재검증한다.

Resolver는 Organization base만 읽지 않는다. Platform hard baseline, current Organization base,
모든 active explicit Collection Privacy Policy Binding, source와 KB stricter revision을 strongest union으로 합성한다.
Collection UUID order는 digest 재현성 전용이고 policy precedence가 아니다. Category action은
`block > manual_review > mask`, detector/manual review requirement는 OR이며 distinct non-null provider
ref가 둘 이상이면 fail-closed한다. Snapshot은 모든 scoped revision과 Collection privacy/source/KB binding
revision, compiled effective digest를 보존하고 finalizer가 같은 set을 다시 resolve한다. Applicable set
또는 effective result 변경은 Organization validity epoch을 증가시키는 invalidating transition이다.

일반 Collection item membership과 order는 검색 grouping/routing state다. Privacy binding은 별도 protected
revision이고 V1에서는 Organization manager만 impact preview/acknowledgement와 exact expected revisions로
변경한다. Active binding이 있는 item unlink는 safe conflict로 막고 binding을 먼저 제거한다. 따라서
`catalog_manage` UI/API가 membership을 바꾸더라도 privacy policy나 Organization validity epoch은 바뀌지 않는다.
Collection archive/restore도 routing lifecycle로만 처리한다. Archived Collection의 active binding은 계속
effective policy에 포함하고 `lifecycle_manage`가 archive/restore해도 privacy epoch을 바꾸지 않는다. Active
binding이 있는 hard delete/policy purge는 cascade하지 않고 binding 제거 전 safe conflict로 차단한다.

Manual-review projection은 staged redacted candidate, bounded safe outcome과 opaque
manifest/generation ref만 읽는다. Review UI 편의를 위해 raw, exact span, provider view/map 또는
reversible mapping을 저장하지 않는다. Raw 확인은 별도 Raw Knowledge Access Flow의 permission,
fresh source ACL, pre-response audit와 retention/legal-hold/purge를 통과해야 하며 Organization
manager review authority가 이를 대신하지 않는다. Current
`GET /api/v1/knowledge/{kb_id}/documents/{document_id}/content`는 이 dedicated flow가 아니므로
Target raw/compliance permission/audit/retention 계약 없이 review surface에 재사용하지 않는다.
Target enforcement cutover는 Nodease가 보존한 upload/fetch 원문 copy를 exact inventory로 freeze하고
valid opt-in과 retention/legal-hold 보존 조건을 모두 충족한 item은 encrypted protected raw artifact로 이관한다.
Protected migration 조건을 충족하지 않고 hold가 삭제를 막지 않는 item은 non-readable fence 뒤 물리
삭제한다. No-opt-in legal-hold conflict는 자동 이관하지 않고 activation을 차단한다. Terminal
`protected_migrated|purged` receipt와 original-copy physical absence가 하나라도
미확정이면 composition root는 enforcement를 활성화하지 않는다. Raw body response는 별도로 해당 item이
`protected_migrated`이고 같은 permission/source/audit/retention gate가 모두 조립된 경우에만 dedicated
surface에서 허용한다. Response 차단은 storage disposition의 대안이 아니며 raw-derived retrieval은
frozen legacy wave만 따른다.

Manual review UI는 provider 유무와 무관하게 동작한다. Mask action은 current candidate에서 추가로 가릴
범위만 선택하고 replacement text나 전체 문서를 전송하지 않는다. 성공하면 새 candidate revision을
표시하되 여전히 pending으로 유지하고 최초 generation expiry를 연장하지 않는다. Candidate body는
encrypted protected staging에 두고 최초 생성 DB time부터 `privacy_review_candidate_ttl_v1 = 7 * 24 hours`를
적용한다. Approve는 UI projection/mutation을 닫되 exact body를 TTL 안의
`approved_pending_finalization` input으로 유지하고, canonical active artifact finalization commit에서만
`purge_pending`으로 전환한다. TTL equality/경과, reject/superseded, generation-bound source/ingestion authority
revoke 또는 stale/abandoned 상태는 즉시 UI projection과 mutation을 닫고 `purge_pending`으로 전환한다.
개별 reviewer 권한 회수는 해당 요청만 hidden zero-write로 닫는다. Approve는 화면에 표시된 exact non-expired candidate revision에만 적용하며
stale conflict에서는 최신 candidate/policy 상태를 다시 조회한다. Pre-approval은 아직 생성되지 않은
content나 이후 generation을 포괄하지 않는다. Terminal block candidate에는 mask/approve control을
렌더링하지 않는다. Raw view를 열 수 있는 별도 권한이 있어도 review mutation request에 raw를 복사하지 않는다.
Application은 candidate를 덮어쓰지 않고 mask/approve/reject를 append-only Privacy Review Decision으로
기록한다. Mask 성공은 successor candidate/manifest, decision과 audit를 원자 확정하고 submitted range는
저장하지 않는다. Approve는 decision/review-state/audit commit 뒤 transaction 밖에서 embedding을 재개하며
finalizer가 current authority/policy/validity/fence를 다시 검사한다.
Knowledge-owned cleanup reconciler는 current claim/fence와 legal hold를 확인해 terminal candidate body를
24시간 안에 purge하고 receipt/tombstone을 남긴다. Legal hold는 physical deletion만 보류하며 review UI나
approval TTL을 재개하지 않는다. Review-capable mode는 protected staging, expiry enforcement와 cleanup/
reconciliation readiness가 모두 준비된 경우에만 조립한다.

Source-managed pending candidate list/detail은 projection 직전에 requester source authorization과
display policy를 fresh 평가한다. Mask/approve/reject application은 mutation commit 직전에 같은 revision을
다시 평가한다. Revoke winner는 ownership-first `404 resource.hidden`으로 닫고 cached candidate/ref/state를
폐기하며 mutation/audit를 남기지 않는다. Manual KB review에는 source gate를 적용하지 않는다.

`external_approved`는 ADR-0067의 public-address guard를 유지한다. `organization_private` adapter는
generic public guard의 private-address 예외가 아니라 별도 `knowledge.detector.organization_private`
profile과 dedicated transport/network isolation을 사용한다. Exact current Detector Egress Approval
Revision, server-owned host/port/CIDR allowlist, all-DNS-result 검증, address pinning, peer/Host/TLS SNI,
HTTPS+mTLS와 no redirect/proxy가 모두 준비돼야 한다. Loopback/link-local/cloud metadata/public/
미승인 private 주소 또는 profile/network readiness 부재는 adapter call 0회다. Approval/private IP/
DNS/mTLS만으로 승인이나 transport readiness를 합성하지 않는다.

Default parsing composition은 network-disabled local parser다. External parser는 detector approval을
재사용하지 않고 explicit Organization opt-in과 source-managed source 또는 manual KB scope opt-in,
exact Raw Parser Egress Approval Revision, server-owned parser revision/credential capability,
`knowledge.parser.external_approved` profile과 ADR-0067 public-address guarded transport 및 bounded no-log/
no-durable-payload readiness를 요구한다. Private raw parser는 별도 Accepted ADR과 dedicated isolation
profile 전까지 지원하지 않는다. 이 경계가 없으면 composition root는 `llamaparse`를 포함한
external strategy를 조립하지 않고 raw upload 전에 `knowledge.raw_parser_egress_unavailable`로 닫는다.
승인된 external parser output도 local normalizer/hard baseline을 반드시 통과한다.

Hard baseline이 terminal `block`을 결정하면 outbound adapter를 호출하지 않는다. Provider가
terminal `block`을 추가하면 embedding/finalizer를 호출하지 않는다. 두 경로는 모두
`knowledge.sensitive_content_detected` safe status와 canonical `policy.block` reason으로
수렴한다. Token-only provider offset은 exact transmitted character boundary와 승인된 versioned
tokenizer mapping이 없는 adapter에서 unsupported다.

Provider nondeterminism은 current fence 아래 staged redacted candidate와 manifest staging
transaction으로 수렴한다. Candidate commit 뒤 retry는 adapter를 다시 호출하지 않고 committed
candidate를 재사용한다. Commit 전 crash/takeover만 bounded 재호출하며 stale generation response나
서로 다른 response를 union/majority-select하지 않는다.

Finalizer가 과거에 만든 active-ready artifact와 current privacy validity는 별도 상태다. V1 manifest는
global platform과 same-Organization validity revision ref/monotonic `validity_epoch`을 snapshot한다. Server-owned
compatibility validator는 policy/baseline/provider/egress-approval transition을
`artifact_preserving|artifact_invalidating`으로 분류하고 모호한 변경은 invalidating으로 닫는다.
Preserving transition은 current revision이 바뀌어도 해당 epoch을 유지한다. Baseline security
supersession은 platform epoch을, stronger Organization action/new required detector와 provider/approval
security invalidation은 Organization epoch을 정확히 1 증가시키며 revision/epoch CAS와 canonical
management audit를 원자 commit한다. V1 Organization invalidation은 해당 Organization의 모든
privacy-gated manifest를 old epoch으로 만든다. Credential rotation과 operational endpoint 변경은
보호 의미가 보존된 preserving transition일 때만 과거 manifest를 유지한다. Pointer projection이
늦더라도 Retrieval Orchestrator의 prefilter/final evidence gate가 두 current epoch을 재검증한다.

`legacy_unverified`는 Privacy Migration Coordinator가 enforcement 전에 고정한 pre-cutoff
artifact에만 적용하는 rollout 상태다. 일반 ingestion application은 이 상태를 만들거나
선택하지 않는다. 각 artifact는 최대 한 wave에만 속하고 `admission_deadline_at`은 새 reindex
admission, `retrieval_cutoff_at`은 legacy visibility를 각각 닫으며 전자는 후자보다 늦을 수
없다. Deadline 전에 admitted된 attempt는 cutoff 뒤 fresh gate/fence를 통과해 compliant
artifact를 finalize할 수 있지만 그동안 legacy retrieval은 닫혀 있다. Cleanup receipt가
확정된 artifact는 rollback candidate에서 제외한다.

Privacy-compliant active pointer finalizer는 pointer swap과 같은 transaction에서 legacy eligibility를
비가역적으로 retire한다. 한 번 compliant pointer가 확정된 document는 active manifest가 나중에
stale/invalid가 되거나 cleanup이 남아 있어도 legacy resolver로 돌아가지 않고 unavailable로 닫힌다.

Allowlist/wave 부재는 legacy grace를 만들지 않는다. Admission과 retrieval은 authoritative DB
time의 half-open `db_now < admission_deadline_at`, `db_now < retrieval_cutoff_at`으로 평가한다.
Scheduler mutation이나 application clock은 cutoff authority가 아니며 Retrieval Orchestrator의
prefilter와 final evidence gate가 frozen membership, current permission/source gate, cutoff 및 frozen
platform/Organization validity epoch와 current 두 epoch equality를 재검증해 stale cache/vector result를
제거한다. Invalidating epoch commit은 bulk item update나 cleanup을 기다리지 않고 관련 legacy evidence를
즉시 제외한다.

Wave header는 freeze 당시 exact platform/Organization validity revision ref+epoch, immutable
`enforcement_activated_at`과 code-owned
`privacy_legacy_grace_v1 = 30 * 24 hours`를 고정한다. `admission_deadline_at <= retrieval_cutoff_at <=
enforcement_activated_at + 30 * 24 hours`여야 하며 deployment rollout은 더 짧게만 설정할 수 있다.
지원되는 grace contract 또는 activation time readiness가 없거나 상한을 넘으면 freeze/activation과
success audit가 모두 zero-write다.

Inventory item은 bounded staging할 수 있으나 frozen wave 전에는 읽기 권위가 없다. Coordinator의
final transaction은 Organization privacy rollout coordination row를 잠그고 exact current legacy
set, staging snapshot과 current platform/Organization validity ref+epoch을 대조해
`legacy_snapshot_revision`과 enforcement epoch를 전진시킨다.
Legacy-producing pointer writer는 같은 epoch CAS를 사용한다. Freeze/writer 경합 loser는 각각
재대조 또는 privacy-gated fresh admission으로 수렴하고 partial staging은 eligibility를 만들지 않는다.
Immutable wave header와 `knowledge.privacy_migration_wave.created` audit는 같은 final transaction에
한 번 commit한다. Aborted/unfrozen staging은 bounded cleanup하고 frozen wave나 cleanup receipt로
투영하지 않는다.

`legacy_artifact_ref`는 `document_version_id`가 있는 versioned artifact와 `NULL`인 legacy
unversioned artifact를 모두 표현하되, 해당 시점의 chunk와 vector/keyword/hierarchy generation
전체 exact set을 하나의 내부 opaque identity로 묶는다. Document 전체 wildcard, 일부 chunk만의
선택 또는 index generation 누락은 허용하지 않는다. Staging 뒤 이 exact set이 추가·삭제·교체되면
final freeze의 set comparison이 실패하고 wave activation/audit를 모두 남기지 않는다.

Legacy cleanup은 Artifact Cleanup Reconciler의 기존 purge completion 경계를 재사용한다.
Coordinator가 exact ref를 non-retrievable `purging`으로 fence하고 durable intent를 commit한
뒤 adapter가 physical generation을 삭제한다. Reconciler는 absence와 generation을 확인해 cleanup
receipt, tombstone과 `knowledge.processing_artifact.purged`를 한 transaction에 exactly-once로
확정하며 completion 실패에 legacy visibility를 복원하지 않는다.

Deployment preflight adapter는 별도 ready 판정을 만들지 않고 Runtime Collection Retrieval과 같은
privacy-aware retrieval-visible resolver를 호출한다. Standalone preview는 blocked 결과도 `200 OK`와
`status="blocked"`로 투영하고 `is_active=false`에서 inactive 저장이 허용되는 availability blocker만
기존 Deployment 계약에 따라 safe `warning`으로 낮출 수 있다. Active create/enable/toggle은
current-valid compliant artifact 또는 아직 retire되지 않고 frozen/current platform·Organization validity
epoch equality와 DB-time cutoff를 통과한 적법한 pre-cutoff legacy artifact가 없으면
fixed `knowledge_privacy_artifact_unavailable` + `reprocess_or_remove_unavailable_knowledge`를 반환하고
`409 deployment.preflight.blocked`로 차단한다. Document/policy/provider identity와 exact count는 표시하지 않는다.
현재 preflight service에는 manifest/validity/legacy-retirement/cutoff 검사가 없으므로 MBA-362에서
구현하기 전 완료로 표시하지 않는다.

### MBA-305 Classification And Processing Boundary

이 boundary는 [ADR-0065](../../decisions/ADR-0065-knowledge-classification-taxonomy-and-processing-profile.md)의
목표 구조이며 현재 package나 endpoint가 구현됐다는 뜻은 아니다.

| Layer | 책임 | 금지 |
| --- | --- | --- |
| Inbound API adapter | Request schema, active organization, use case dependency, response/error mapping | Taxonomy graph, permission, profile precedence 또는 transaction 직접 판단 |
| Classification application use case | Permission, current assignment/lock/revision/content freshness, registry/taxonomy version, impact acknowledgement, audit와 reindex intent 순서 조율 | SQLAlchemy expression, provider SDK, HTTP response shape |
| Pure taxonomy/assignment/profile policy | Forest/impact validation, authority와 content freshness precedence, atomic set validation, profile resolution과 stale/deprecated 판단 | DB session, FastAPI, Celery, storage/provider 접근 |
| Registry/assignment/profile-catalog PostgreSQL adapter | Organization-scoped immutable version, draft/catalog/impact row lock/CAS, assignment/suggestion projection과 authoritative mutation-bound impact revision increment | Permission 또는 profile policy 독자 결정, raw content/credential projection, async impact counter를 publish authority로 사용 |
| Audit adapter / UnitOfWork | Authoritative mutation과 allowlisted canonical audit를 같은 transaction으로 확정 | Raw label 설명, source path/title/URL, prompt/completion 저장 |
| Classifier outbound adapter | Egress/redaction gate 뒤 bounded suggestion candidate 생성 | Effective assignment write, lock 해제, security/permission 변경 |
| Reindex application/adapter | Parent/current KB authority와 applicable fresh source gate를 receipt lookup보다 먼저 평가하고 return/commit 전 revision을 재검증한다. Authorized exact replay, preview validation, decision admission, existing-artifact satisfaction 또는 exact-one embedding binding을 가진 durable build, immutable job actor/binding의 batch/finalization gate, pointer CAS와 최초 result의 canonical audit를 조율한다 | Source gate보다 receipt/result를 먼저 조회하거나 replay audit을 중복 생성하거나 audit 실패 뒤 receipt/result를 남기거나 cross-actor/credential reuse로 job actor/binding을 교체하거나 receipt/job을 capability로 사용하거나 expired token으로 fresh admission하거나 revoked actor/credential, stale unlocked axis 또는 `purging|purged` artifact를 사용하거나 existing active artifact를 in-place overwrite하거나 output hash를 job identity로 사용 |
| Artifact availability/cleanup adapter | Blocking pin과 retention, purge generation, physical cleanup 및 append-only tombstone을 outbox/reconciler로 수렴 | Manifest provenance만으로 영구 보존하거나 current/citation/legal hold를 무시하거나 tombstone 전에 재사용 가능 상태로 두기 |

`security_classification`은 existing Knowledge security/final-evidence policy가 계속 소유한다.
Classification application은 이 값을 type/topic/profile에서 파생하거나 낮추지 않는다. Source-managed
classifier input은 source authorization과 provider egress를 별도 통과한다.

Legacy multi-document KB는 exact document/version adapter를 사용하고, 모호한 KB-wide request는
application error `knowledge.classification_migration_required`로 닫는다. Target document-level KB
cutover 뒤 canonical KB-scoped command로 수렴한다.

Assignment current-validation은 server가 content, taxonomy와 document-type registry의 changed-dimension set을
계산하고 단일 변경에는 각각 `content_reviewed|taxonomy_updated|registry_updated`, 둘 이상에는
`combined_review`만 허용하며 변경이 없으면 reason을 저장하지 않은 `unchanged`다. Server set에 content가 포함된
validation만 content-confirming이며 effective `content_read`와 applicable current source/display authorization을
commit 시점까지 직렬화하고 Organization manager도 이를 우회하지 않는다.
Taxonomy/registry-only validation은 raw content gate를 요구하거나 content review를 주장하지 않는다. 성공은
effective set과 axis별 source/lock/최초 snapshot을 보존하고 manual-confirmation provenance,
last-validated refs와 audit만 commit한다. New canonical content에서 locked axis는
review-recommended eligible 상태를 유지하고 unlocked stale axis는 validation 전 resolver/materialization에서
제외한다. Resolver input revision이 바뀌어도 materialization input fingerprint가 같고
active build integrity가 valid하면 새 decision manifest와 `satisfied_existing` link로 current decision
pointer만 전환한다. Fingerprint가 바뀌면 durable reindex job을 만들되 old active ready version을 계속
검색한다. New staging artifact가 ready이고 immutable job execution actor의 current permission/source authorization,
embedding binding lifecycle/relation/permission/provider-routing revision, canonical content, assignment,
registry/taxonomy, policy, override, Organization/Platform profile
catalog/selectability와 resolved
profile의 exact revision vector를 재검증한 finalizer만 build manifest, `satisfied_new` link와
decision/artifact pointer를 확정한다.

### MBA-232 Runtime Candidate Resolver Boundary

구성요소는 다음으로 분리한다.

| Layer | 책임 | 금지 |
| --- | --- | --- |
| Shared pure contract/policy | explicit audience/request/snapshot/result, direct-first/round-robin/dedupe/budget, safe bucket | SQLAlchemy, FastAPI, Celery, Gateway/Workflow concrete import |
| Workflow Engine application use case/port | request validation, snapshot port 1회 호출, pure policy 적용, whole-resolution failure mapping | SQL query, Gateway response schema, provider/retrieval side effect |
| PostgreSQL outbound adapter | fresh `REPEATABLE READ, READ ONLY` transaction, selected Collection별 pre-window LATERAL cap, fixed transaction evaluation time, membership/readiness/permission/materialized provenance bulk projection | organization-wide discovery, live connector/source call, cross-invocation cache |
| Workflow Engine composition | session factory, adapter와 use case 조립 | LLM node business policy와 graph parsing |

Gateway의 기존 `KnowledgeCandidateResolver`는 Builder recommendation/deployment
preview 경계다. Missing Collection scope에서 route-safe subset 또는 direct-KB fallback을
사용할 수 있으나 MBA-232 runtime resolver에는 적용하지 않는다. Runtime
missing/empty Collection IDs는 Collection stream 0개다. Builder/preflight adapter,
Workflow graph와 LLM node/retrieval wiring은 MBA-233에서 연결한다.

Runtime audience는 `AuthenticatedAudience(organization_id, user_id)`와
`AnonymousPublicAudience(organization_id)`의 closed union이다. Anonymous audience에
owner/builder/deployment owner/credential principal/service account를 합성하지 않는다.
Source-managed authenticated 후보는 materialized `SourceAuthorizationProvenance`만
사용하고 live `check_access*`/cache를 호출하지 않는다. Public exposure primitive가
없는 동안 source-managed anonymous 후보는 모두 제외한다.

`KnowledgeCollectionItem`은 lifecycle을 갖지 않는다. Present row만 membership이고
Collection/child KB lifecycle과 KB readiness를 별도로 평가한다. Snapshot/repository/
authorization infrastructure failure는 partial candidate를 반환하지 않는 retryable
whole-resolution failure다. Budget cap은 successful safe warning이며 downstream
retrieval timeout과 구분한다.

### MBA-233 Workflow Collection Routing Integration

| Component | 책임 | 금지 |
| --- | --- | --- |
| Shared Workflow Knowledge Reference Parser | 두 graph list의 shape, canonical UUID, display snapshot, per-list 20 cap을 pure validation하고 configured order/deduped ID를 제공한다 | Graph mutation, silent slicing, permission/DB 조회 |
| Route-safe Collection Query Service | active organization/lifecycle, non-source-deleted sync state와 current editor effective `route`를 SQL query scope에 먼저 적용하고, authorized result를 정렬·제한한 뒤 UUID와 optional safe label만 projection한다 | 권한 확인 전 row cap, Management response 재사용, raw name/description/child count, runtime authorization |
| Workflow Knowledge Reference Service | Editable graph write 전에 direct KB active/non-source-deleted/retrieval-visible/effective `use`/source gate와 Collection active/non-source-deleted/`route`를 current editor로 검증하고 whole-write failure를 반환한다. Reference 없는 legacy graph는 구조 검증 뒤 authorization query를 생략한다 | Collection child expansion, saved label/Client capability 신뢰, runtime lease 발급 |
| Deployment Preflight | 두 list의 structure, lifecycle/sync eligibility, direct KB retrieval-visible readiness와 server-derived audience/public gate를 재귀 graph에 적용하고 safe bucket/action을 반환한다 | Child ID/exact hidden count 공개, preflight를 runtime capability로 재사용 |
| Workflow LLM Integration | explicit execution audience와 두 configured ID list로 MBA-232 resolver를 invocation당 한 번 호출하고 ordered KB ID를 Retrieval Orchestrator에 전달한다 | Gateway resolver import, LLM node 내부 permission SQL, owner/credential fallback |
| Public/Observability Projector | public graph에서 두 reference list를 제거한다. Explicit direct KB의 기존 authorized lineage와 KB-local rank는 유지할 수 있다. Collection-derived evidence는 child KB/document/chunk identity와 KB-local rank를 제거하고 최종 병합 evidence rank만 result/quality trace에 남기며, audit은 node-level count bucket으로 집계한다 | Collection identity/provenance, child resource identity/rank, raw graph/query/source/provider payload 저장 |

Builder는 고정 KB와 Knowledge Collection을 별도 selector group으로 표시한다. 각 group은
독립 `n/20` limit을 가지며 Collection membership이 실행 시점에 다시 계산된다는 설명을
표시한다. Picker에서 사라진 saved item은 generic unavailable chip으로 보존하고
사용자가 제거하거나 권한이 복구되기 전 새 저장을 차단한다. Builder 안에서
Collection 생성/삭제/permission/membership을 관리하지 않는다.

Agent Builder는 [ADR-0061](../../decisions/ADR-0061-agent-builder-hierarchical-knowledge-selection.md)에 따라 route-authorized Collection과 use-authorized 하위 KB를
계층 후보로 표시한다. Collection 선택은 runtime 동적 routing, 하위 KB 선택은 직접
binding이며 둘은 독립 상태다. 동일 KB는 모든 Collection 위치에서 같은 selection state를
공유한다. Runtime은 direct KB와 Collection child를 KB ID 합집합으로 중복 제거하고
MBA-232 materialized provenance/readiness 결과를 사용한다.
화면 후보는 점수 계산과 안정 정렬 뒤 Collection 최대 20개, 고유 KB 최대 20개로 제한하고
약 3개 행 높이의 내부 스크롤로 표시한다.

Conversation Memory target adapter는 Knowledge Permission Helper의 bulk 결과를 `decision`, `principal_kind`, opaque `authorization_decision_revision`, `resource_revision`, `policy_revision`, `evaluated_at` contract로 투영한다. Source-managed KB의 source ACL revision은 decision revision에 반영한다. Lifecycle, KB permission, source ACL 중 필요한 revision이 없으면 allow를 추정하지 않고 `unknown`을 반환한다. Anonymous public audience에는 subject ID/revision을 합성하지 않는다.

Retrieval Orchestrator는 최종 evidence와 함께 KB/document version, organization, sensitivity와 authorization-safe reference를 `RuntimeDataDependencyEnvelope`로 발급한다. Raw title/path/URL/content/ACL은 envelope에 포함하지 않는다. Client나 Workflow node가 canonical Knowledge dependency를 발급할 수 없고, V1에서는 answer content에 영향을 준 모든 Knowledge dependency를 필수로 취급한다.

## UI Surfaces

| Surface | 목적 |
| --- | --- |
| Knowledge Collections | Collection 목록, 상세, 생성/수정/archive, item 관리, permission grant/revoke, visibility 상태를 표시한다 |
| KB Detail | Document-level KB lifecycle, active version, sync state, permission state를 표시한다 |
| KB Permission Management | Admin/settings의 권한 UI에서 KB별 team grant와 user direct grant를 표시, 생성, 갱신, 회수한다 |
| Knowledge Delegation | Organization manager가 Team 우선으로 Knowledge domain action을 부여·회수하고 만료 상태를 확인한다. Domain action과 KB content access를 분리해 표시한다 |
| Source Connector Setup | Connector config, egress-safe test/preview, ACL mapping status를 관리한다 |
| Sync Remediation Queue | Stale/unmapped/ambiguous ACL, failed sync, tombstone, retry/dead-letter status를 표시한다 |
| Agent Knowledge Settings | Collection routing scope 또는 explicit KB를 선택한다. 허용된 safe candidate만 표시한다 |
| Skill Management / Playground Candidate | 향후 Skill version, freshness, eval status, publication/review 상태를 표시할 수 있는 후보 surface다. 실제 작성/테스트/승인 요청 UX와 Workflow Playground 통합 여부는 아직 확정하지 않으며, 표시한다면 safe metadata만 사용한다 |
| Audit/Citation Detail | Redaction-safe citation과 retrieval summary를 표시한다. Raw content는 별도 raw/compliance surface에서만 사용한다 |
| RAG A/B Compare | LLM node 단위 RAG strategy, token, cost, citation summary를 비교한다 |

### KB Detail Source Processing UI

`POST /api/v1/rag/upload`로 등록된 source document는 초기 상태가 `pending`일 수 있으며, chunk/embedding 생성이 끝나기 전까지 RAG 검색 대상이 아니다.

- KB detail은 `can_register_initial_document=true`인 경우에만 `첫 source 등록` action을 표시한다. Client는 `documents.length`, `can_write`, KB 이름으로 이 capability를 재구성하지 않는다.
- 하나의 Document가 `pending`, `failed`, `processing`, `completed` 중 어느 상태로 존재해도 independent `소스 추가` action은 표시하지 않는다. Retry/process/delete는 기존 Document action으로 분리한다.
- Occupied KB에는 별도 KB를 만든 뒤 Knowledge Collection에서 묶으라는 안내와 Collection 관리 이동 경로를 제공한다. 파일을 물리적으로 병합하거나 Collection 권한이 child KB content 권한을 부여한다고 표현하지 않는다.
- Stale UI가 registration을 제출해 `409 knowledge.document_slot_occupied`를 받으면 raw 오류나 기존 document identity를 표시하지 않고 모달을 닫아 detail capability를 다시 조회한다.

- KB 상세의 source 목록은 `pending` document에 `처리 시작` action과 "처리 시작 전에는 RAG 검색에 사용되지 않는다"는 safe 안내를 표시한다.
- `failed` document는 같은 document settings 화면으로 들어가는 `재처리` action을 제공한다.
- 처리 중 document의 progress UI는 active organization UUID를 포함한 authorization-scoped SSE URL만 연다. Active organization이 없으면 stream을 열지 않고 safe 안내를 표시하며, Gateway의 KB `read` 거부 응답 뒤 자동 재연결하지 않는다.
- Source upload 성공 후 UI는 KB 상세 source 목록으로 돌아오며, 방금 등록된 `pending` source를 포함한 목록에서 처리 시작 action을 제공한다. FILE source는 document settings 화면에서 원본 preview를 렌더할 수 있으므로 업로드 직후 자동으로 상세 화면을 열지 않는다. Client는 원본 content를 active organization header가 포함된 API 요청으로 받고 ephemeral Blob URL만 viewer에 전달한다. PDF는 Edge를 포함한 브라우저 기본 PDF viewer를 사용하는 `object`와 `noopener noreferrer` 새 탭 fallback을 제공하며, content 응답은 `nosniff`를 유지한다. Blob URL은 document scope 전환 또는 unmount 때 revoke한다. HTML로 변환되는 Office 문서와 text 계열은 `allow-same-origin`만 허용한 scriptless sandbox iframe에서 렌더링하며, preview iframe에는 `allow-downloads`와 `allow-scripts`를 추가하지 않는다. 다운로드는 별도의 명시적 사용자 action에서만 시작할 수 있다. 기존 `completed` document를 열 때는 자동으로 KB 상세로 이동하지 않으며, 현재 document scope에서 active processing 상태를 관찰한 뒤 완료된 경우에만 완료 후 이동한다.
- 지식 테스트 모달은 모델 목록, 일반 검색, AI 답변 요청을 하나의 active organization scope에 묶고 모두 같은 `X-Organization-Id`를 전송한다. Organization, KB 또는 modal open scope가 바뀌면 진행 중인 검색 generation을 무효화하고 이전 scope의 결과를 렌더링하지 않는다. Stored organization과 아직 rerender되지 않은 Client state가 다르면 이전 organization header로 새 검색을 시작하지 않는다. AI 답변은 현재 organization에서 사용 가능한 chat model이 선택된 경우에만 제출할 수 있다.
- KB/document `read` response의 metadata는 safe progress/state projection만 사용한다. UI는 `api_config`, encrypted source config, DB/connector connection identifier나 raw source reference가 detail payload에 존재한다고 가정하지 않는다. Document settings UI는 별도 `GET .../edit-config`를 KB `write` 경계에서 호출하고, 현재 KB/document scope의 `editable=true` configuration hydration이 끝나기 전에는 DB/API preview와 process action을 disabled 처리한다. Route 전환 시 source-specific state와 gate를 즉시 reset하고 cleanup 이후 도착한 이전 scope 응답은 폐기한다. API URL/header/body나 encrypted value를 화면 state, session storage key, toast/log에 복원하지 않는다.
- Document processing UI는 process/approval API가 성공한 뒤에만 local status를 active processing으로 바꾼다. Initial fetch, process/approval, SSE와 polling callback은 자신이 시작된 active organization/KB/document scope를 캡처하고 organization 또는 route 전환, cleanup 뒤 도착한 응답을 폐기한다. Scope ref가 이미 바뀐 이전 render의 handler는 process/analyze/preview/approval 요청 자체를 시작하지 않는다. 완료 후 이동은 같은 scope에서 active processing 상태를 관찰한 경우에만 예약하므로 이미 처리 중인 문서를 지켜보는 흐름은 완료 후 이동할 수 있지만, 비용 승인 취소·요청 실패·초기 `completed` 상태는 이동 intent를 만들지 않는다.
- Process/sync/resume 응답의 `dispatch_deferred=true`는 실패 확정이 아니라 committed durable job의 broker wake-up이 지연됐다는 의미다. UI는 임의 background task를 재생성하지 않고 status/SSE를 다시 조회한다. Dead-letter retry는 API가 `retryable=true`로 투영한 경우에만 제공하며 기존 job ID를 client authority로 사용하지 않는다.
- Document status UI는 Gateway가 반환한 fixed public processing message와 generic failure만 표시한다. Persisted `error_message`, `processing_current_step`, Redis value를 raw UI string으로 간주하지 않으며 Client가 internal exception detail을 fallback으로 표시하지 않는다.
- 이 UI는 hidden document, 권한 없는 source path/title, raw source content를 표시하지 않는다.

### KB Permission Management UI

- MBA-176에서는 기존 admin/settings permission surface를 확장해 KB team permission과 user direct permission을 함께 관리한다. MBA-231은 같은 관리 영역에 Organization manager 전용 Knowledge domain delegation panel을 추가하되 KB resource grant와 시각적으로 분리한다.
- UI는 organization member 목록을 grant 대상 후보로 사용하되, 조직에 속해 있다는 사실만으로 KB `use/read/manage` 권한이 생긴다고 표시하지 않는다.
- User direct grant 생성/수정에서는 `viewer`, `operator`, `builder`, `manager`만 선택할 수 있다. `none`은 선택지로 제공하지 않고, 권한 회수는 삭제 action으로 표현한다.
- Effective permission 표시는 organization manager override와 team/user direct grant 중 가장 강한 additive allow로 계산된 값을 사용한다. User direct grant가 team grant를 낮추거나 deny할 수 있는 것처럼 표시하지 않는다.
- `can_manage_kb`가 없는 사용자에게는 grant action을 숨기거나 disabled 처리하되, 최종 차단은 Gateway API가 수행한다.
- Domain delegation은 Team을 기본 선택으로 제공하고 user direct grant는 예외 경로로 둔다. `catalog_manage`, `permission_delegate`, `lifecycle_manage`, `sync_manage`의 허용 범위와 content-plane 비상속을 각 action 설명에 표시한다.
- Domain `permission_delegate` actor에게는 자신과 자신이 속한 Team이 grant 대상으로 보이더라도 content-plane grant가 차단됨을 safe 안내한다. 최종 self/own-Team 차단은 Gateway가 수행한다.
- Workflow builder/RAG 선택과 runtime retrieval의 canonical ready 기준은 active ready
  DocumentVersion이다. Active pointer가 없는 compatibility data만 completed Document의
  retrieval-visible legacy unversioned chunk fallback을 사용할 수 있다. ADR-0070 enforcement
  뒤에는 frozen `legacy_unverified` wave membership, frozen/current platform·Organization validity epoch
  equality, `privacy_legacy_grace_v1` hard max와 DB-time half-open cutoff를 prefilter/final evidence gate
  모두에서 통과해야 한다. Allowlist/wave 부재, stale validity epoch, cutoff equality/경과와 stale
  cache/vector result는 fallback 대상이 아니다. `completed` status만으로 ready를 판정하거나
  active pointer가 있는데 legacy chunk로 우회하지 않는다.

KB detail UI는 manual KB recommendation용 safe metadata 편집 surface를 제공할 수 있다. `safe_label` 자동 생성 버튼과 `kb_safe_topics` 자동 생성 버튼은 각각 KB name/description에서 sanitizer, length cap, secret/url/path removal을 적용한 값을 채우며, 저장 버튼은 전용 `PATCH /api/v1/knowledge/{kb_id}/safe-metadata`로 allowlisted 필드만 전송한다. `can_manage_safe_metadata=true`일 때만 이 surface를 표시하고, `can_edit_settings=false`이면 이름·설명·embedding model·소스 추가/재처리 같은 `write` 동작을 표시하거나 활성화하지 않는다. Archive/restore는 `can_manage` 또는 lifecycle domain capability, hard delete는 별도 Organization manager acknowledgement capability를 사용한다. Source-managed KB의 raw source title/path/url은 이 surface에 표시하거나 recommendation input으로 사용하지 않는다.

### Classification And Processing Policy UI

- KB detail의 classification panel은 effective document type, topic set/primary, document-type/topic axis별
  source, lock, freshness와 resolver-eligible 상태,
  taxonomy/registry/assignment revision, review-required와 reindex-required 상태를 표시한다.
- KB `read` actor는 safe projection만 본다. KB `write` actor는 unlocked assignment와 suggestion
  review, current-validation, profile preview/reindex를 사용할 수 있고 KB `manage` actor는 lock/unlock과
  explicit profile override options/read/set/clear를 사용할 수 있다. 같은 active organization의 Organization
  manager는 ADR-0034 override로 이 KB-scoped capability를 별도 grant 없이 사용할 수 있지만 source/display gate를
  우회하지 않는다. UI capability는 server 응답에서 파생하며 role 이름만 보고 권한을 추측하지 않는다.
  Source-managed classification/override/resolve read 또는 mutation이 `resource.hidden`으로 닫히면 panel은 이전
  assignment/type/topic/security projection, lock/override/resolver state, picker option과 preview token을 즉시 폐기하고
  generic unavailable state만 표시한다. KB grant나 manager role로 source gate를 통과했다고 추정하거나 stale
  panel cache를 유지하지 않는다.
- `current_validation_required` axis에만 current-validation action을 제공한다. UI는 server가 반환한
  options의 `current_validation_changed_dimensions`에 따라 단일 content/taxonomy/registry 변경은 각각
  `content_reviewed|taxonomy_updated|registry_updated`, 둘 이상은 `combined_review`만 제출한다. Content가 변경된
  validation은 `can_confirm_current_content=true`일 때만 확인 action을 제공하고,
  권한 회수 또는 stale source gate에서 기존 확인 상태를 폐기한다. Taxonomy/registry-only 변경은 content-plane
  권한을 요구하거나 content를 열거나 확인했다고 표시하지 않지만 source-managed resource gate 실패에서는 같은
  generic unavailable state로 닫는다. 두 options field는 capability가 아니며 submit 시 server가
  source-managed source/display gate를 항상 다시 확인하고 content changed set에서만 `content_read`와 raw policy를 추가로 확인한다. `review_required` axis에는 같은 validation 재시도 버튼을 제공하지 않고 current option을 사용한 complete
  assignment replacement flow를 제공한다. UI는 edited axis만 `manual_axes`에 넣고 valid한 다른 axis를 complete
  payload에 prefill하되 그 value/source/lock을 보존한다. Locked 반대 axis가 rule/suggestion source여도 edited axis의
  저장을 막지 않으며 missing/deprecated ID를 자동 replacement로 선택하거나 silent merge하지 않는다.
  Missing/deprecated explicit profile override의
  `review_required`는 selectable override 교체 또는 clear recovery로 연결한다. Classification invalid axis가
  locked이면 KB `manage` 또는 Organization manager에게 unlock을 먼저 제공하고 그 뒤 complete replacement로
  연결한다. Write-only actor에게는 잠금을 우회하는 replacement 대신 manager remediation 필요 상태를 표시한다.
- Lock action은 current axis에서만 활성화하고 server가 반환한 assignment revision과 canonical content revision을
  함께 보낸다. `current_validation_required`는 validation 뒤, `review_required`는 complete replacement 뒤에만
  lock을 다시 제공한다. Unlock은 stale/review-required 상태에서도 recovery action으로 유지한다. Canonical content가
  바뀐 경우 locked axis는 `review_recommended`, unlocked axis는 `current_validation_required`로 구분하고 stale
  unlocked value가 profile/filter에 사용된 것처럼 표시하지 않는다.
- Picker는 stable ID를 server에 보내되 bounded approved label/path snapshot만 표시한다. Deprecated
  topic은 effective assignment provenance에서 disabled/review-required로 보이지만 options
  response와 신규 선택, ranking/profile mapping에서는 제외된다. Topic picker는 최대 32개 선택을 허용하고
  33번째 선택을 제출하지 않지만 API의 server-side bound를 보안 경계로 유지한다.
- Current taxonomy가 없는 Organization에서는 topic picker를 empty no-taxonomy state로 표시하고 type-only
  assignment submit에 null taxonomy + empty topic set을 사용한다. Current taxonomy가 생긴 뒤에는 server가
  반환한 exact version을 요구하며 stale null snapshot을 자동 보정해 submit하지 않는다. Empty-topic
  assignment는 assigned taxonomy가 null 또는 prior version인지와 새 taxonomy가 empty/non-empty인지에 관계없이
  invalid-topic review가 아닌 `current_validation_required` action을 표시한다. Non-empty topic이 새 empty
  taxonomy에서 missing이면 `review_required`로 구분한다.
- Suggestion은 effective assignment와 시각적으로 구분하며 uncalibrated score를 정확도 백분율로
  표시하지 않는다. 목록은 server cursor로 기본 25개씩 조회하고 state filter/다음 page를 제공하되 total 또는
  hidden count를 추정하지 않는다. Page마다 권한이 회수돼 `resource.hidden`이 반환되면 기존 candidate와
  confidence를 즉시 폐기하고 generic unavailable state로 닫는다. Accept는 candidate가 제안한 axis 중
  non-empty subset과 reindex 영향 preview를 명시한다. Current selected axis가 manual이면 lock 여부와 무관하게
  해당 axis accept를 비활성화하되 선택하지 않은 manual axis와 그 authority는 보존한다. Blocked candidate를 적용하려는 actor에게는
  assignment 편집 form을 candidate set으로 채우되 일반 manual replacement 확인을 다시 요구한다.
  Candidate set이 current 값과 같아도 어느 axis source라도 non-manual이면 manual confirmation action을 no-op으로
  비활성화하지 않는다. Client는 suggestion accept request에 manual authority 우회 flag를 만들지 않는다.
  Accepted assignment의 상세 provenance는 `generator_kind`와 opaque `generator_contract_ref`를 공통으로 표시한다.
  Deterministic kind는 approved rule-set/version만, AI kind는 server가 허용한 classifier/model/
  prompt-template/calibration refs와 bounded confidence bucket만 표시하고 non-applicable provider field나 raw
  prompt/rationale/score를 복원하지 않는다.
- Reviewer가 `accepted_axes`를 바꾸면 Client는 같은 nullable assignment revision으로 accept-preview를 다시
  요청하고 server가 반환한 axis별 value/authority effect, profile-resolution change, `reindex_required`와
  active-ready availability만 confirmation에 표시한다. `value_changed`는 authority 동시 변경을 포함하고,
  value가 같은 `authority_changed` 및 `unchanged`와 구분해 표시한다. Unselected axis는 `unchanged`다.
  Preview 성공 전 accept를 활성화하거나 current
  classification/profile GET을 조합해 impact를 Client에서 추측하지 않는다. Opaque
  `accept_preview_revision`은 current organization/KB/actor와 selected suggestion 화면 메모리에만 보존하고
  accept request에 돌려보낸다. Commit response loss가 의심되는 accept는 serialized request를 바꾸지 않고 같은
  preview revision/axes/precondition으로 재전송하며 local expiry만으로 exact retry를 선제 차단하지 않는다.
  Server가 기존 terminal result를 반환하면 새 assignment/audit가 생긴 것으로 해석하지 않고 그 결과로
  reconcile한다. Terminal match가 없는 fresh request의 scope/axis change, expiry 또는 stale `409`에서는
  candidate/assignment를 다시 읽고 preview를 재요청하며 local result를 자동 merge하지 않는다.
  Source-hidden에서는 candidate, preview와 token을
  즉시 폐기한다. Token/digest를 URL, storage, toast, analytics 또는 Client log에 남기지 않는다.
- Explicit profile override control은 server options에서만 profile을 선택하고 option이 준 strict
  `profile_revision_ref={catalog_scope, profile_revision_id}` 전체를 재구성하거나 scope를 버리지 않고 set에
  제출한다. Current override source, nullable strict scoped `resolved_profile_revision_ref`, revision, nullable current
  `profile_policy_version`과 opaque full
  `override_resolver_revision`을 별도 current override query로 읽어 표시한다. Set/change/clear는 policy version을 `expected_profile_policy_version`, token을
  `expected_override_resolver_revision`으로 돌려보내며 stale conflict에서 local projection을 적용하지 않고 override/options를
  모두 다시 조회한다. Token은 current organization/KB/actor 화면 scope 메모리에만 유지하고 다른 scope에서
  재사용하거나 authorization으로 해석하지 않는다. 성공 뒤에는
  `reindex_required`만 갱신하며 별도 preview와 reindex action을 유지한다.
  Missing/deprecated/incompatible override는 hidden profile identity를 표시하지 않고 generic
  review-required 상태와 override revision으로 clear recovery control을 유지한다. 다만 source-managed KB의
  source gate가 `resource.hidden`이면 이 recovery control과 cached override/options/resolver revision도 폐기하며
  clear를 local capability로 허용하지 않는다.
- Profile preview는 server resolution source
  `override|type_primary|type|primary|org_general|platform_general|platform_default`를 그대로 표시한다. Current
  profile-policy가 null이고 valid explicit override가 없는 경우에만 `platform_default`를 표시하고 exact default
  revision은 read-only다. Default-required branch의 unavailable은 generic remediation, selectable override set
  recovery와 기존 active-ready 유지 상태로 표시하며 Client fallback 또는 clear action을 만들지 않는다.
- Profile preview의 opaque `resolution_revision`은 현재 organization/KB 화면 scope 메모리에만 두고
  reindex request에 돌려보낸다. Raw/internal fingerprint로 해석하거나 URL, storage, toast, log에
  보존하지 않으며 scope 전환/expiry/stale conflict에서 폐기하고 preview를 다시 조회한다.
- Reindex Client는 새 사용자 intent마다 `crypto.randomUUID()`의 lower-case hyphenated 36자 값을 만들고
  `Idempotency-Key` header 하나로만 전송한다. Body field, upper-case/brace 변환, duplicate header와 raw key
  logging을 만들지 않는다. 응답을 받지 못한 retry는 동일 key와 동일 canonical typed request tuple을
  유지하며 preview만 새로 받아 old key와 new body를 조합하지 않는다. Terminal result를 받거나 사용자가
  resolver input을 바꾼 새 intent에서만 새 UUID를 만들고 key를 `localStorage`, URL, analytics 또는 persistent
  Client state에 저장하지 않는다.
- Reindex request의 durable idempotency key와 exact request envelope은 응답을 받을 때까지 같은 화면
  scope 메모리에서 유지한다. 응답 유실 뒤 같은 request를 재시도하면 server receipt의 기존 safe result를
  사용하고 새 key/token을 조합하지 않는다. Receipt가 없거나 request가 달라 expired/stale conflict가
  오면 preview를 다시 조회한다. KB 권한 또는 applicable source authorization이 취소된 actor에게 기존 result를 표시하지 않고 화면 scope의 receipt/result state를 폐기한다.
- Stale revision `409`는 현재 assignment/taxonomy를 다시 불러오고 사용자 변경을 자동 merge하지
  않는다. Legacy ambiguous KB는 migration-required remediation을 표시한다.
- Organization profile editor는 `processing_profile_schema_v1`의 integer size `64..8192`와 overlap
  `0..floor(size/2)`를 numeric control에 적용하되 API validation을 최종 경계로 유지한다. Empty/string/float/
  boolean을 숫자로 coerce하거나 out-of-range 값을 silent clamp하지 않는다. Catalog capability가 더 작은
  limit를 반환하면 editor도 그 값을 사용하고 stale capability validation에서 draft를 자동 재작성하지 않는다.
  Profile-policy editor는 `processing_profile_policy_v1` complete document를 편집한다. Matcher kind에 따라
  type+primary/type/primary field만 표시하고 target은 server profile option의
  `catalog_scope`와 exact revision을 사용한다. Raw 2,000-rule cap을 넘기기 전에 추가 command를 비활성화하지만
  API cap을 우회 가능한 Client security boundary로 간주하지 않는다. Organization/Platform general control은
  별도 nullable field이며 Platform general이 null인 incomplete draft는 저장·재개할 수 있어도 validate/preview/
  publish를 활성화하지 않는다. PATCH는 화면의 complete rules/general state를 보내고 omitted rule을 server가
  merge한다고 가정하거나 stale draft를 자동 병합하지 않는다.
- Organization manager taxonomy, profile catalog와 profile-policy UI는 bounded identity/version list와 exact detail을
  통해 기존 draft를 다시 열고 published/superseded history를 검사할 수 있다. Draft validation,
  publish와 bounded impact/reindex bucket을 제공하되 publish 자체가 기존 KB 전체 reindex를
  시작한다고 표현하지 않는다. Editor는 server `draft_revision`을 PATCH/validate/preview/publish에
  돌려보내고 validate/impact response의 taxonomy/profile-policy/registry와 Organization/Platform catalog
  compatibility snapshot도
  publish에 그대로 보낸다. Stale conflict에서 newer draft나 cross-resource pointer를 자동 merge하거나
  덮어쓰지 않고 모두 다시 불러온다. Topic 0개 draft는 valid하게 표시하되 publish confirmation에서 기존
  topic assignment의 review-required 영향과 topic-rule policy 선행 정리 필요 여부를 명시한다.
  Taxonomy publish control은 same-snapshot impact preview를 먼저 실행하고 server의 opaque
  `impact_preview_revision`과 explicit acknowledgement를 함께 보낸다. Token은 화면 scope 메모리에만 유지하고
  expiry/snapshot conflict에서 자동 재확인하거나 old acknowledgement를 재사용하지 않는다. Impact 0개도
  confirmation을 생략하지 않고 hidden identity/exact denied count를 추정하지 않는다.
  Impact UI는 server의 `taxonomy_impact_bucket_v1`과 `none/small/medium/large`만 표시하고
  `none=0`, `small=1..10`, `medium=11..100`, `large=101+` 경계 밖의 exact count나 hidden
  identity를 추정하지 않는다. Contract version이 바뀌면 old confirmation을 폐기하고 새 preview를 요구한다.
  Affected는 candidate/current taxonomy의 topic-axis freshness와 eligible topic/primary tuple이 달라지는
  processing-eligible current assignment다. Exact version-only 또는 동일한 stale tuple은 세지
  않는다. Reindex candidate는 그 subset에서 active-ready artifact의 비교 가능한 current decision fingerprint와
  candidate fingerprint가 달라지는 물리 변경 또는 current decision/fingerprint가 없어 동일 materialization을
  증명할 수 없는 active-ready legacy artifact를 뜻한다. Profile revision/source/status만 달라지고 fingerprint가 같음이 증명되면 reindex bucket에서 제외하며
  publish를 assignment migration 또는 job 시작으로 표현하지 않는다.
  Editor는 max 1,000 topic과 2,000 replacement edge의 complete forest를 detail에서 한 번에 복구한다.
  1,001번째 topic과 2,001번째 edge를 추가하지 못하게 하되 server validation을 대체하지 않는다. 새 topic에는
  UUID `draft_topic_key`만 만들고 stable `topic_id`는 server response/mapping에서 받는다. 응답 유실이나 stale
  conflict에서는 draft detail을 다시 조회해 current complete-tree key-to-ID mapping과 current `draft_revision`을
  복구하고 subsequent mutation은 server ID만 사용한다. Current-mapped local key를 다시 submit하거나 mutation을
  자동 replay하지 않는다. Topic 제거 성공 뒤 해당 mapping을
  local state에서도 제거하고 같은 key를 다시 쓰지 않는다. Server가 제거된 key를 다시 받으면 새 stable ID를
  발급하므로 Client가 old ID와 동일하다고 가정하지 않는다.
  Editor는 server normalizer 기준 sibling collision을 표시하고 root를 같은 sibling scope로 취급한다.
  Replacement graph는 depth 8까지 허용하며 depth 9, cycle, 다른 Organization/missing target을 submit 또는
  publish 가능한 상태로 표시하지 않고 replacement를 자동 assignment migration으로 표현하지 않는다.
  최초 taxonomy/profile-policy publish는 server snapshot의 nullable current pointer를 그대로 보내며 null을
  임의 version이나 sentinel 문자열로 치환하지 않는다.
- Organization profile catalog의 create control은 exact `{}`를 보내고 server가 반환한 null config/base,
  `draft_revision=0` first-draft shell을 editor state에 보존한다. Shell은 complete config PATCH 전 validate/publish
  control을 활성화하지 않으며 response 유실 뒤 임의 local identity를 만들지 않고 list/detail로 복구한다.
  Catalog는 manager에게 successor draft, allowlisted token/parser/representation/embedding config validation,
  immutable published history와 deprecate control을 제공한다. Successor draft action은
  history에서 exact same-identity `published` revision을 base로 선택해 `base_revision_id`를 보내며 mutable latest나
  현재 list 순서를 암묵적 base로 사용하지 않는다. 성공 response의 base ID와 `draft_revision=0`을 editor state에
  보존하고 wrong-owner/lifecycle failure에서 빈 draft를 local success로 만들지 않는다. Published config는
  edit/delete control을 표시하지 않고 current policy/override reference로 deprecate가 차단되면 hidden KB나
  exact reference count 없이 먼저 policy/override를 교체하라는 safe remediation만 표시한다. Platform profile과
  `platform_default_profile_revision`은 read-only option/projection이고 publish/deprecate/default mutation control을
  제공하지 않는다. Current default target의 deprecate가 차단되면 platform registry owner가 pointer를 먼저 옮겨야 한다는
  generic remediation만 표시한다. Profile publish/deprecate 성공 뒤 catalog revision을 다시 읽되 reindex가
  자동 시작됐다고 표시하지 않는다. Profile-policy publish는 server가 반환한
  `organization_profile_catalog_revision`과 `platform_profile_catalog_revision`을 각각 expected field로 보내고
  어느 catalog라도 stale이면 두 option set, exact Platform default와 draft compatibility를 다시 불러온다.
  Profile-policy editor는 Organization general rule 부재를 유효한 정책으로 허용하되 approved Platform general
  fallback 부재는 publish할 수 없게 한다. Current policy 안의 missing/stale mapping은 그 policy의 Platform
  general로 fallback할 수 있지만 invalid explicit override는 generic fallback으로 보정하지 않고 review-required remediation으로 표시한다.
  Profile-policy publish control은 taxonomy token과 섞지 않고 server의
  `profile_policy_impact_bucket_v1`, `affected_resolution_bucket`, `reindex_candidate_bucket`, opaque
  `impact_preview_revision`과 expiry를 사용한다. `none=0`, `small=1..10`, `medium=11..100`, `large=101+` 경계만
  표시하고 exact count/identity를 추정하지 않는다. Assignment가 없는 general-fallback target은 affected에 포함될 수
  있다. Valid override가 계속 우선하는 target, active artifact가 없는 target, 그리고 active-ready legacy artifact는
  있지만 current decision/fingerprint가 없어 보수적으로 reindex candidate가 된 target의 의미가 서로 다르므로
  Client가 KB 목록을 조합해 bucket을 재계산하지 않는다. Publish는 fresh preview의 compatibility snapshot,
  contract version, opaque token과 explicit acknowledgement를 그대로 전송한다. Preview가 `none`이어도 confirmation을
  생략하지 않고 stale/expired/scope conflict에서는 policy를 publish하거나 old acknowledgement를 재사용하지 않는다.
  Token은 해당 Organization/actor/candidate editor 메모리에만 유지하고 URL, storage, analytics 또는 log에 저장하지
  않는다. Publish 성공은 policy pointer만 바꾸며 assignment migration 또는 reindex job이 시작됐다고 표시하지 않는다.
- Nested resource lookup이 safe `404`이면 Client는 suggestion/profile/policy version의 존재나 상태를
  추정하는 메시지를 만들지 않고 generic unavailable state로 복구한다.

### Knowledge Collection Management UI

Knowledge Collection 관리 UI는 Workflow Builder가 아니라 Knowledge 관리 영역에 둔다.

필수 surface:

- Collection 목록: safe name/description, manual/system-managed, lifecycle/sync state, visibility, bucketed linked/active KB count, caller action flags를 표시한다.
- Lifecycle 관리: active/archived 탭을 분리하고 archived manual Collection에만 restore action을 제공한다. 복구 성공 뒤 active 목록을 다시 조회하며 restore가 permission이나 Workflow route 또는 privacy binding을 부여·제거한다고 표시하지 않는다. Binding detail과 제거 control은 Organization manager 전용 privacy flow에만 두고 delegated lifecycle UI에는 policy identity나 binding detail을 노출하지 않는다. System-managed 또는 source-deleted 제한은 고정 safe reason으로 표현한다.
- Collection 생성/수정: organization manager 또는 domain `catalog_manage`가 private manual Collection을 생성한다. 관리 form은 raw 관리용 `name`과 Workflow picker용 `안전 표시 이름`을 별도 필드로 제공하고 둘 다 nonblank일 때만 새 Manual Collection 생성을 제출한다. 편집 가능한 Manual Collection은 `safe_metadata.safe_label`을 안전 표시 이름 필드에 복원하며, 값이 없는 기존 Collection에는 보완 필요 안내를 표시하고 label 입력 전 정보 저장을 비활성화한다. UI는 관리용 `name`을 label로 자동 복사하지 않고 create/update request의 `safe_metadata.safe_label`로 명시적으로 전송한다. Update는 서버가 반환한 다른 safe metadata를 보존하면서 label을 교체한다. Delegated create는 client 입력과 무관하게 private다. `is_system_managed`나 public visibility는 일반 create/edit form에서 직접 설정하지 않는다.
- Collection 생성과 public visibility control은 `domain-capabilities`의 분리된 boolean capability를 사용한다. Client는 Organization manager 여부나 action 조합으로 권한을 재구성하지 않으며 capability refresh 실패 시 이전 create/public 허용 상태를 즉시 닫는다. Collection list의 중복 management capability는 호환 projection이고 보안 판정 근거가 아니며, 모든 mutation은 Gateway가 다시 인가한다.
- Collection 상세: item, permission, visibility, sync/system state를 분리해서 표시한다.
- Item manager: linked KB safe label, lifecycle/sync state, rank, `can_manage_kb`, `can_use_kb`를 표시한다. 유효한 safe label이 없고 caller가 KB `read`를 통과하지 못하면 generic label을 사용하며, domain `catalog_manage`만으로 manual KB `name`을 표시하지 않는다. Private membership은 `collection.manage` + KB `manage` 또는 domain `catalog_manage`, public membership은 Organization manager acknowledgement 경계를 따른다. 이 화면은 routing membership만 관리하고 privacy binding control을 포함하지 않는다. Active privacy binding 때문에 unlink가 막히면 safe fixed 안내와 Organization manager 전용 privacy impact flow 이동 action만 표시한다. 500개 이하 item은 위/아래와 keyboard 조작으로 local draft를 만들고 current order revision과 전체 item set을 명시적으로 저장한다. Save/cancel/dirty/stale 상태를 구분하고 conflict 때 local draft를 조용히 덮어쓰지 않는다.
- Permission panel: bounded server-side subject combobox를 사용하고 Team을 기본값으로 둔다. Subject type/query/cursor가 바뀌면 stale page를 폐기하고 loading/empty/error/retry/next-page를 제공하되 email이나 raw id를 visible fallback label로 쓰지 않는다. `collection.manage` 또는 domain `permission_delegate` actor가 `read`, `route`, `manage`, `sync` additive allow를 grant/revoke할 수 있다.
- Public visibility warning flow: organization manager, explicit acknowledgement, safe exposure summary를 요구한다.
- Public Collection item link/unlink/reorder도 같은 public exposure warning과 acknowledgement를 요구한다.
- Collection role preset은 Viewer, Workflow Router, Maintainer, Sync Operator를 제공하되 저장 시 explicit action row를 transactionally 적용하고 KB `use`가 포함되지 않음을 표시한다. Bundle 회수도 같은 action 집합의 현재 row를 한 번에 제거하며 저장된 role이나 inheritance처럼 표현하지 않는다.
- Bulk permission 관리: 같은 subject와 bundle을 선택한 1~50개 Collection에 grant/revoke를 한 요청으로 적용한다. UI는 all-or-nothing임을 설명하고 partial success를 만들거나 표시하지 않으며 성공/실패에서 hidden target identity를 노출하지 않는다.
- Permission row는 Team과 User direct source를 분리해 표시한다. User direct row 회수 뒤 Team grant가 남을 수 있음을 안내하고 action 조합을 role provenance로 재구성하지 않는다.
- Collection sync panel: active Manual Collection에서 `collection.can_sync` 또는 domain `can_manage_sync`가 있고 canonical child eligibility scan의 `sync_supported=true`일 때만 요청 버튼을 제공한다. 한 click의 UUID idempotency key를 요청 확정까지 재사용하고 queued/running job에서는 중복 click을 막는다. Detail 진입 시 latest job을 조회하고 queued/running 동안 3초 polling하며 terminal, unmount, Collection 변경 시 polling을 중단한다.
- Sync 상태는 `queued/running/succeeded/partially_failed/failed/cancelled` text label과 `none/started/progressing/most/complete` 범주형 progress를 표시한다. Safe reason은 Client 고정 문구로 매핑하고 raw server detail을 그대로 렌더하지 않는다.

금지 surface:

- raw source title/path/url/principal 표시.
- hidden KB name/id 또는 exact denied count 표시.
- Collection manage 권한을 KB content use 권한처럼 표시.
- Workflow Builder 화면에서 Collection 생성/삭제/권한관리를 주 기능으로 제공.
- raw Collection `name`/`description`을 route-safe picker label로 fallback하거나 안전 표시 이름에 자동 복사.

## State Model

| Object | States |
| --- | --- |
| KB lifecycle | `active`, `archived`, `deleted` |
| KB sync state | `synced`, `syncing`, `sync_failed`, `sync_disabled`, `source_deleted` |
| DocumentVersion | `staging`, `indexing`, `ready`, `failed`, `superseded` |
| Source ACL freshness / mapping | `fresh`, `stale`, `unmapped`, `ambiguous`, `unverified`, `revoked` |
| Sync run | `queued`, `leased`, `running`, `succeeded`, `failed`, `dead_lettered`, `cancelled` |
| Skill freshness | `fresh`, `stale`, `review_required`, `deprecated` |

`source_deleted`는 KB sync/source state이며 document version status가 아니다. Version이 과거 source 삭제 시점의 snapshot임을 표현해야 하면 `source_deleted_snapshot` 같은 historical stale reason을 사용한다.

Permission Helper가 source-managed가 아닌 KB를 평가할 때는 source ACL freshness enum 대신 `not_source_managed` 같은 safe sentinel을 반환할 수 있다. 이 값은 fresh source ACL을 의미하지 않고, source ACL gate가 적용되지 않는 KB임을 나타낸다.

Purge는 일반 KB lifecycle state가 아니다. Processing artifact purge는 ADR-0065의 generation-fenced
`purging` intent, physical delete, tombstone+`knowledge.processing_artifact.purged` completion transaction과
same-generation recovery 계약을 따른다. 그 밖의 retention/legal-hold purge, raw source artifact purge와 source
tombstone cleanup은 구현 전에 별도 retention policy, audit action/reason code와 recovery contract가 필요하다.

## Interaction Flows

### 빌더 단계 LLM node RAG 옵션 구성

1. Workflow Builder 요청과 active organization을 검증한다.
2. `KnowledgeCandidateResolver`가 Builder actor와 server-resolved context 기준으로 authorized safe candidate set 또는 server-issued reference를 만든다. MBA-145 Agent Builder MVP에서는 Knowledge Skill body/checklist를 prompt context로 직접 로드하지 않는다.
3. Skill Context Loader는 후속 target 흐름이다. 후속 기능에서 Skill을 사용할 때만 선택된 skill의 redaction-safe body/checklist를 visibility, display policy, freshness/eval gate 이후 필요 시점에 로드한다.
4. Builder는 Knowledge RAG Recommendation Adapter를 통해 safe KB recommendation과 LLM node RAG option 후보를 받는다. Adapter input은 raw natural language 전체가 아니라 `StructuredRequest` 기반 `intent_summary`, `node_purpose_summary`, `knowledge_requirement`, `pending_resolution_ref`, `safe_workflow_context_summary`, KnowledgeCandidateResolver의 server-issued reference다. 같은 backend 내부 service call에서는 full safe candidate set 객체를 사용할 수 있지만, HTTP/serialized boundary에서는 reference만 사용한다. Adapter는 Collection을 실행 candidate로 반환하지 않고 `source_collection_summary`로만 제공하며, 현재 LLM node schema에 맞게 `knowledgeBases`로 materialize 가능한 KB 목록을 반환한다. 새 LLM node와 추천 옵션의 검색 기본값은 `scoreThreshold=0.3`, `topK=5`다.
5. Hidden resource를 추론할 수 있는 aggregate count는 bucket 처리하거나 생략한다.
6. Builder output에는 raw source id/url/path/title, raw principal, raw ACL fact, exact hidden/denied count, raw content, raw skill body를 넣지 않는다.
7. 생성된 workflow의 LLM node의 RAG 옵션은 실행 시점에 execution subject 기준으로 collection route, KB permission, source ACL/requester authorization, final evidence policy를 다시 통과해야 한다.

### Runtime Collection Retrieval

1. Workflow runtime이 server-owned canonical organization과 explicit authenticated audience 또는 anonymous public audience를 구성한다. Optional user/owner fallback은 사용하지 않는다.
2. MBA-232 resolver는 configured direct KB와 명시 selected Collection만 받아 fresh PostgreSQL `REPEATABLE READ, READ ONLY` snapshot을 연다. Missing/empty Collection scope는 0개다.
3. Authenticated audience에서는 direct KB `use`, selected Collection `route`와 각 child KB `use`, applicable materialized source provenance를 bulk 평가한다. Anonymous audience에서는 active public Collection membership을 평가하고 source-managed KB를 fail-closed 제외한다.
4. Active lifecycle, `source_deleted` exclusion과 current-valid active ready version을 적용한다. ADR-0070 enforcement 뒤 legacy fallback은 privacy-compliant active pointer가 한 번도 확정되지 않았고 eligibility가 retire되지 않은 document만 frozen wave membership, frozen/current platform·Organization validity epoch equality, `privacy_legacy_grace_v1` hard max와 DB-time cutoff를 prefilter/final evidence gate 모두 통과할 때 허용한다. Invalidating epoch commit은 item projection/cleanup 전 즉시 legacy를 제외한다. Compliant pointer가 존재하거나 과거에 확정된 뒤 manifest가 stale/invalid가 된 경우 legacy로 돌아가지 않는다. Collection item은 row presence만 membership으로 보고 parent/child lifecycle을 별도로 평가한다.
5. Direct configured order를 먼저 유지하고 selected Collection configured order의 round-robin으로 남은 20-KB budget을 채운다. Canonical KB ID로 dedupe하고 first provenance를 보존한다.
6. Candidate 0개는 provider/retrieval 전 `safe_no_result`, budget 제한은 fixed safe warning을 가진 성공이다. Snapshot/repository/authorization infrastructure failure는 partial candidate 없이 retryable whole-resolution failure다.
7. MBA-233에서 연결되는 Retrieval Orchestrator는 resolved active/ready KB만 검색하고 evidence를 merge한다. `query_rewrite_mode`는 safe candidate set을 넓히지 않는다.
8. Source-of-Truth Tier는 authorized evidence 안에서 ranking, tie-break, conflict resolution hint로만 사용한다.
9. Final evidence policy와 evidence sufficiency check는 LLM prompt, answer generation, citation preview emission 전에 실행한다.
10. 근거가 부족하면 추측 답변을 만들지 않고 safe no-result 또는 insufficient-evidence response로 닫는다.
11. Answer/citation/audit/trace summary는 redaction-safe allowlist만 사용한다.

### Explicit KB Retrieval

1. 요청과 active organization을 검증한다.
2. Explicit KB를 resource-hiding matrix에 따라 resolve한다.
3. Collection route permission은 생략할 수 있다.
4. KB use helper, source ACL/requester authorization, final evidence policy는 항상 적용한다.
5. Retrieval과 citation은 auto mode와 같은 redaction-safe 규칙을 따른다.

### Workflow Runtime RAG

1. Workflow runtime이 run context에서 execution subject를 resolve한다. Gateway는 `internal_chatbot` 인증 run에서 current user를 subject로 전달하고 공개 `chatbot` run에는 subject를 전달하지 않는다.
2. Execution subject가 있으면 Knowledge Permission Helper가 해당 subject 기준으로 KB permission과 source ACL/requester authorization을 평가한다.
3. Execution subject가 없으면 Workflow owner, deployment owner, builder, `user_id`를 silent fallback으로 쓰지 않는다. Runtime은 anonymous public-only로 낮추고, active public collection에 연결된 active KB만 candidate로 남긴다.
4. Public collection은 `KnowledgeCollection.safe_metadata["visibility"] == "public"`으로 판정한다. 누락 또는 다른 값은 private로 취급한다. Source-managed Collection과 source-managed KB는 Public Exposure Policy Store의 valid source/connector public exposure approval도 통과해야 candidate로 남는다. Approval primitive가 없는 현재 anonymous runtime은 `source_identity_id`가 있는 Collection을 child KB 유형과 무관하게 fail-closed 제외하며, source-managed public 후보는 warning이 아니라 `source_public_exposure_required` blocked state로 표시한다.
5. Workflow가 Knowledge Skill을 사용할 경우 skill visibility, freshness/eval, safe metadata gate도 execution subject가 있을 때 같은 subject 기준으로 평가한다. Anonymous public-only runtime은 skill 선택만으로 private KB 후보를 넓힐 수 없다.
6. `general`, `permission_scoped`, `task_aware` 등 모든 운영 RAG mode는 subject 기반 gate 또는 anonymous public-only gate와 final evidence gate를 통과한다.
7. Authorized 후보가 없으면 query embedding capability와 provider operation을 만들지 않는다. 후보가 있으면 projection의 distinct canonical embedding model마다 별도 `query_embedding` capability/lease를 사용하고 같은 model 후보만 invocation-local vector를 공유한다.
8. Retrieval strategy, query rewrite, source tier, skill 차이는 gate 이후 authorized/public evidence를 얼마나 넓게 또는 정밀하게 선택하는지에만 영향을 준다.
9. Evidence sufficiency policy가 insufficient로 판정하면 workflow node는 근거 부족 응답이나 안전한 분기 결과를 반환해야 하며 문서에 없는 정책 해석을 생성하지 않는다.
10. Trace/A-B summary는 safe citation metadata, token/cost/latency, strategy, query rewrite 적용 여부, evidence sufficiency 결과, skill id/version/freshness/eval status만 노출한다. Raw query/vector, credential principal과 capability scope를 저장하지 않는다.

### Source Sync And Version Activation

1. Scheduler가 connector sync lease를 획득한다.
2. Connector worker가 guard/adapter를 통해 source item과 source ACL을 가져온다. MCP/API source는 allowlist operation만 사용한다. Slack 계열 초기 baseline은 channel을 collection으로, thread/huddle recap/canvas/bot-generated meeting summary/pinned-message group을 document-level KB로 매핑한다. DM/raw audio/raw transcript는 기본 수집하지 않는다.
3. Content Safety Scanner가 file type allowlist, active content 차단, archive cap과 scan timeout/unknown을 적용한다.
4. Default는 network-disabled Parser Isolation Worker다. External parser는 Organization과 applicable source-managed source 또는 manual KB opt-in, exact Raw Parser Egress Approval Revision, `knowledge.parser.external_approved` profile과 ADR-0067 public-address transport readiness가 있을 때만 raw bytes를 승인된 public HTTPS parser에 제한 전송하며, 그 전에는 upload 0회로 닫는다. Private raw parser는 별도 Accepted ADR 전까지 지원하지 않는다.
5. Normalizer가 local/external parser의 untrusted output에 `privacy_text_unicode_14_0_nfc_lf_v1`을 적용하고 local hard baseline을 실행한다.
6. Effective Privacy Policy Resolver가 Organization base, 모든 explicitly bound Collection과 applicable source/KB stricter revision을 합성한다. Policy가 요구하면 exact same-Organization provider revision을 trust-tier별 dedicated guarded transport로 호출하고 complete segment/fingerprint/UTF-8 span을 검증한다.
7. Shared Privacy Core가 baseline/provider span을 additive union하고 local deterministic masking으로 redacted canonical text와 safe Privacy Decision Manifest를 만든다.
8. Ingestion concurrency guard가 source item 또는 document-level KB 단위 owner-token/fencing lock을 확보한다.
9. Ingestion이 redacted canonical content에서만 새 document version, chunk, embedding, external index artifact를 staging 상태로 생성한다.
10. Finalizer는 current actor/source/effective policy/provider/parser revision과 fence, 모든 artifact completeness를 재검증한 뒤 짧은 transaction에서 active version, legacy eligibility retirement, `content_hash`, processing fingerprint와 embedding model을 함께 확정한다.
11. Outbox/recovery scanner가 orphan cleanup, stale worker finalization 차단, ephemeral raw/view cleanup과 crash recovery를 처리한다.

### Live-linked Retrieval

1. Runtime이 execution subject와 source subject mapping state를 확인한다.
2. Source-side search가 requester-scoped이면 해당 subject 기준으로 검색한다.
3. Requester-scoped search가 없고 opaque source ref-only search만 있으면 ref 후보를 받은 뒤 `check_access_batch` 또는 bounded `check_access` fallback으로 재확인한다.
4. Broad service-account search가 authorization 전 title, snippet, count, score를 반환하는 source는 Live-linked 일반 retrieval 후보에서 제외한다.
5. Metadata, citation, audit/trace summary는 runtime authorization과 display policy를 통과한 safe field만 사용한다.

### Raw Content View

1. Active organization과 KB visibility를 검증한다.
2. Raw/compliance permission과 source-managed KB의 fresh source ACL을 검증한다.
3. Retention, legal hold, purge state를 검증한다.
4. Content 반환 전에 raw access audit을 기록한다.
5. Raw content는 dedicated raw/compliance surface에서만 반환한다. Agent answer, retrieval context, prompt construction, SSE stream은 redacted canonical text만 사용한다.

## Performance And Scalability

- Permission helper는 candidate resolution에서 per-KB query를 피하고 bulk evaluation을 지원해야 한다.
- Candidate lookup에는 KB 중심 index와 user-candidate index가 모두 필요하다.
- Public Gateway route는 Retrieval Orchestrator 호출 전에 KB `use`를 확인하고, Workflow runtime은 MBA-232 resolver가 반환한 authorized candidate만 전달한다. Retrieval Embedding Model Projection은 이 후보 선별 뒤에만 실행한다.
- 일반 implicit-model retrieval의 `LLMModel` 조회는 authorized KB 수와 distinct model 수에 무관하게 invocation당 최대 1회다. Explicit verified model과 authorized candidate 0건 경로는 0회다.
- Workflow fanout에는 query vector와 immutable scalar model binding만 전달한다. ORM row, credential, secret은 전달하지 않으며 runtime KB model과 binding identifier가 다르면 provider와 vector store 호출 전에 제외한다.
- Query vector는 node invocation과 canonical embedding model별 한 번만 생성하고 invocation 사이에 보존하지 않는다. Provider operation identity나 usage dimension에 KB ID 또는 hidden candidate count를 포함하지 않는다.
- Projection은 invocation 사이에 cache하지 않는다. 다음 invocation은 model active/type/ambiguity 상태를 다시 읽으며, MBA-289 Authorized Retrieval Port 이관 시 같은 projection 계약을 adapter 내부로 흡수한다.
- 쿼리 수 상한은 회귀 차단 기준이다. 지연 시간은 실제 PostgreSQL과 운영 환경에서 관찰하되 기능 완료를 단일 timing threshold에 결합하지 않는다.
- 초기 candidate cap은 `max_candidate_kbs=5000`, `max_route_collections=20`, `max_retrieval_kbs=20`, `max_chunks_per_kb=8`, `max_total_chunks=50`이다. 이 값은 운영 baseline이며 제품의 고정 계약이 아니다.
- Candidate cap, fanout concurrency, timeout, partial failure behavior는 [implementation_baseline.md](implementation_baseline.md)의 baseline을 시작점으로 삼고, operations policy로 조정 가능해야 하며 운영 배포 전에 load test를 거쳐야 한다.
- 가능한 경우 KB/version filter를 포함한 단일 vector/keyword query를 우선한다. Backend가 지원하지 못하면 concurrency와 timeout cap이 있는 bounded per-KB fanout을 사용한다.
- Workflow의 bounded per-KB fanout은 authorized candidate와 사전 계산 query vector만 받는 application scheduler가 소유한다. 현재 baseline은 invocation당 동시 검색 최대 5개이면서 Workflow Engine 프로세스 전체 native blocking-I/O data worker도 최대 5개다. KB별 최대 10초, 최초 task 제출 전부터 시작하는 caller aggregate 30초이며 마지막 1초는 cancellation과 transaction 정리를 기다리는 데 예약한다. Queue 대기와 cleanup 대기를 aggregate deadline에 포함하고 deadline 뒤 새 KB search를 시작하지 않는다.
- 각 시작된 KB search는 독립 SQLAlchemy session과 PostgreSQL read-only transaction을 사용하고 `organization_id + knowledge_base_id`로 KB를 조회한다. Adapter는 task 시작 시 단조시계 기준 절대 deadline을 고정하고 operation이 발생시키는 모든 SQL 직전에 cancellation과 남은 budget을 재검증한다. PostgreSQL transaction-local `statement_timeout`은 각 SQL마다 남은 정수 millisecond 이하로 축소하므로 여러 statement가 각각 최초 timeout을 새로 사용할 수 없다. Guard는 operation 뒤 connection에서 제거한 다음 transaction을 rollback하고 session을 close한다. Timeout/cancel의 DBAPI query cancel은 data worker와 분리된 프로세스 전체 최대 2개의 native control worker에서 실행해 gevent hub를 막지 않는다. 등록 해제된 callback은 실행하지 않고, 이미 실행 중인 callback은 완료된 뒤에만 해당 session을 rollback/close하여 pool로 반환된 연결에 늦은 cancel이 도달하지 않게 한다. Outer Workflow session은 native worker와 공유하지 않는다. Scheduler는 cleanup reserve 동안 종료를 기다리되 협조하지 않는 non-DB 작업 때문에 caller hard deadline을 연장하지 않는다. Hard deadline 뒤 완료된 task는 evidence를 게시할 수 없다.
- Fanout completion 순서는 evidence 결과를 바꾸지 않는다. Scheduler는 candidate ordinal 기준으로 결과를 반환하고, 기존 global score/source-tier 정렬, dedupe, top-k, final evidence policy와 citation projection이 최종 순서를 결정한다. `safe_no_result`는 성공 evidence를 유지할 수 있지만 `fail_node`는 남은 task를 취소하고 partial evidence를 사용하지 않는다.
- Authorized RAG retrieval trace는 `candidate_resolution_latency_ms`, `query_embedding_latency_ms`, `retrieval_fanout_latency_ms`, `slowest_search_latency_ms`, `evidence_policy_latency_ms`만 aggregate stage latency로 허용한다. 값은 0~300,000 범위의 finite non-negative integer millisecond이며 실행하지 않은 stage는 생략한다. Candidate 0건, empty query 등 hidden/resource-hidden 상태와 구분하지 않는 `safe_no_result` 경로는 exact stage latency를 모두 생략한다. Per-KB timing, raw query/vector, hidden resource identity와 provider/DB raw error는 저장하지 않고 이 다섯 필드는 일반 Workflow result metadata, chatbot/SSE와 citation projection에 포함하지 않는다.
- Workflow LLM node는 `context_variable`이 지정된 경우 해당 referenced variable의 정제된 값만 ephemeral retrieval query로 사용한다. 설정이 없는 legacy graph만 렌더링된 user prompt 전체를 사용하며 raw query는 durable trace, audit, log 또는 cache key에 저장하지 않는다.
- Opt-in CrossEncoder rerank는 권한을 통과한 chunk의 redacted canonical text를 메모리에서 복호화한 뒤 사용한다. 프로세스 cache의 최초 또는 model 변경 초기화는 원본 native lock으로 직렬화하고 완성된 model만 게시한다. 저장 암호문을 ranking model input으로 전달하거나 복호화 실패 때 암호문으로 fallback하지 않는다.
- MBA-232 runtime resolver는 candidate ID/authorization을 invocation 사이에 cache하지 않는다. 향후 candidate cache를 별도 승인할 경우 permission/freshness revision을 포함해 ACL revocation이 stale candidate를 무효화해야 한다.
- Skill candidate cache key에는 skill version, freshness state, eval state, source version reference를 포함해 stale skill이나 source tier 변경이 즉시 무효화되어야 한다.
- Query rewrite cache를 둘 경우 key에는 rewrite mode, safe template id, skill version, permission/freshness epoch를 포함해야 하며 raw rewritten query를 durable cache key나 trace key로 사용하지 않는다.
- `llm_assisted` query rewrite는 추가 latency와 LLM cost를 만든다. 운영 배포 전 rewrite timeout, token/cost budget, fallback, load shedding, usage logging 기준을 load test에 포함한다.
- DB source sync나 shared vector save path도 같은 document-level KB에 대한 chunk replacement를 직렬화하거나 versioned chunk set + active pointer 방식으로 처리해야 한다.

## Implementation Phases

- Phase 1: egress negative paths, protected source identity, basic sync, content safety/parser isolation, redaction, active version swap, transactional outbox insert, fencing token, recovery scanner smoke.
- Phase 2: source ACL freshness, content cursor와 ACL/permission watermark 분리, Knowledge Permission Helper, KB `use` + source ACL two-gate, source-policy grant inactive lifecycle.
- Phase 3: multi-KB caps, final evidence recheck, resource hiding matrix, retry/dead-letter transition, partial result behavior.
- Later: golden questions, source tier tuning, LLM-assisted rewrite, advanced rerank.

## Security And Privacy

- Raw source id/url/title/path, raw source ACL, raw content, prompt/completion, provider raw response, credential value, secret은 audit/trace/log에서 제외한다. Raw/compliance access log는 safe reference와 decision만 저장한다.
- Internal document metadata는 encrypted value도 credential-bearing configuration으로 취급한다. KB/document read response는 allowlist projector를 통과하고 unknown field는 default deny하며, API config와 connection/source identifier는 response, error, audit, trace, log로 복사하지 않는다.
- DB source 설정 저장 전에는 Connection owner를 확인하고 opaque `connection_id` 외 Connection detail을 metadata에 복제하지 않는다. Background processor는 외부 dial 직전 독립 runtime snapshot provider로 execution subject owner를 다시 확인하고 최소 configuration을 만든 뒤 authorization session을 닫는다. Missing/malformed/deleted/owner-changed/non-owner는 동일한 resource-hiding 실패, 저장소 장애는 safe temporary failure, 복호화 실패는 fallback 없는 configuration failure로 닫고 adapter를 호출하지 않는다. Processor는 ORM Connection과 encrypted field를 직접 해석하지 않는다.
- Connection reference 저장·교체·삭제만 `Connection -> KnowledgeBase -> Document/DocumentVersion` 순서의 bounded row lock을 사용한다. Existing Document reference writer는 Connection 다음 Document를 fresh read lock하고 최초 조회 revision과 다르면 `connection.reference_conflict`로 전체 rollback한다. Lifecycle UoW는 reference metadata commit까지 소유하며 PostgreSQL lock/flush/commit의 timeout/deadlock/serialization failure를 전체 rollback과 safe transient reason으로 처리하고, 기타 commit/store failure를 safe unavailable로 닫는다. External DB/storage/provider I/O, chunking과 embedding은 Connection lock transaction 밖에서 수행한다. Runtime fetch는 connect/statement timeout과 batch·row·byte cap을 적용한다 ([ADR-0053](../../decisions/ADR-0053-connection-transaction-and-lock-boundary.md)).
- KC sync panel은 `can_sync || can_manage_sync` authority가 있고 `sync_supported=true`인 active Collection에서만 실행을 활성화한다. `can_sync`는 권한, `sync_supported`는 POST와 동일한 canonical child eligibility 결과이므로 어느 하나도 다른 하나를 대신하지 않는다. Panel은 latest/specific job을 3초 bounded polling하고 status, 범주형 progress, fixed safe reason만 표시하며 exact child count/identity나 source/connection/config 오류를 DOM에 만들지 않는다. Request가 진행 중이거나 job이 queued/running이면 ref 기반 double-submit gate와 disabled 상태를 함께 적용한다.
- Domain revoke는 inactive subject 복원을 요구하지 않는다. Existing permission row를 organization scope 안에서 lock/delete하고 audit와 원자 commit해 stale delegated capability를 제거한다.
- Domain revoke repository는 organization/subject/action predicate와 `FOR UPDATE`를 하나의 SQL statement로 유지한다. Compile contract와 opt-in disposable PostgreSQL test가 cross-org isolation, audit rollback, concurrent exactly-one delete/audit를 검증한다.
- Collection membership 관리 capability는 KB label read capability가 아니다. Safe label이 없으면 독립 KB `read`를 통과한 caller만 manual KB `name`을 볼 수 있다.
- Source-derived display metadata는 user-facing 저장 전에 redaction, 길이 제한, display-policy approval을 거쳐야 한다.
- `verify=false`, HTTPS downgrade, 승인된 operation profile/guarded transport 밖의 custom HTTP client, private/link-local/metadata IP target, cross-origin redirect와 ambient proxy 기반 guard 우회는 금지한다.
- DB adapter arbitrary SQL과 SSH adapter arbitrary command execution은 향후 ADR이 좁은 use case를 승인하지 않는 한 connector test/preview/sync path에서 금지한다.
- 일반 사용자와 workflow 작성자 화면에는 권한/정책상 제외된 문서명, raw source title/path/url, exact denied count를 표시하지 않는다. 관리자/감사 화면도 별도 권한과 display policy가 없으면 safe/bucketed summary만 표시한다.
- 운영 `general RAG`는 권한 없는 문서를 포함하는 mode가 아니다. 모든 RAG mode는 권한 gate를 통과하며, A/B 테스트의 차이는 authorized evidence 안에서 broad retrieval과 task-aware retrieval을 비교하는 것이다.
- Knowledge Skill은 source of truth나 permission decision이 아니다. Skill body/resource에는 raw content, raw source title/path/url, hidden KB id, restricted document list를 저장하지 않는다.
- Query rewrite 결과 원문은 raw prompt처럼 취급한다. Durable audit/trace/log에는 raw rewritten query를 저장하지 않고 safe strategy summary만 저장한다.
- Evidence sufficiency reason은 권한 없는 문서의 존재나 개수를 암시하지 않는 safe reason class로만 표시한다.
- Code-bearing skill은 별도 sandbox/approval/egress/resource-cap gate가 닫히기 전까지 Knowledge 실행 시점 경로에서 실행하지 않는다.
- Retention purge와 cleanup worker는 terminal state와 legal hold를 확인하고, concurrent worker가 같은 row를 중복 처리하지 못하도록 row lock, marker, idempotency key 중 하나를 사용해야 한다.

## Workflow Citation Projector

- Workflow Engine의 Citation projector는 runtime candidate resolver가 허용한 후보 중 최종 prompt에 실제 포함된 evidence만 입력으로 받는다.
- manual KB는 승인된 `safe_metadata.safe_label`, source-managed KB/Collection은 active하고 display policy가 approved인 source identity의 `safe_display_name`만 사용자 라벨로 사용할 수 있다. 조건을 충족하지 않으면 일반 라벨로 fail-closed한다.
- Collection 경유 evidence의 child section과 child resource identity는 projector 경계에서 제거한다.
- projector 결과는 LLM node instance의 실행별 ephemeral 상태로만 유지하고, Answer node data ancestry를 따라 최종 응답에 투영한다. control-only LLM node와 subworkflow의 sidecar는 상위 응답으로 승격하지 않는다.

## Accessibility

- Collection과 KB state badge에는 색상만이 아니라 text label이 있어야 한다.
- Error/remediation state는 admin/preflight 또는 이미 visible로 판정된 resource context에서만 permission denied, source ACL stale, sync failed, hidden resource를 구분한다. 일반 사용자/작성자 context에서는 hidden name/path를 누출하지 않는 safe reason class로 낮춘다.
