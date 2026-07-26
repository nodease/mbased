# Knowledge 구현 baseline

Status: Draft

이 문서는 MBA-105 Knowledge 통합 구현자가 따라야 할 임시 구현 baseline을 한곳에 모은다. [ADR-0017](../../decisions/ADR-0017-knowledge-integration-provisional-implementation-baseline.md)을 기능별 구현 기준으로 풀어 쓴 문서이며, Workflow RAG의 `execution_subject` 부재 처리는 [ADR-0018](../../decisions/ADR-0018-workflow-rag-anonymous-public-only-runtime.md)을 따른다. MCP/API connector 기반 incremental sync와 Live-linked/public exposure 보완 경계는 [ADR-0020](../../decisions/ADR-0020-knowledge-mcp-incremental-sync-boundary.md)을 따른다. ADR과 충돌하면 ADR이 우선한다. 관련 기능은 auth, workflow, agent-builder, connectors, audit-tracing, llm-credentials, cost-optimizer다. API path, migration column, UI copy의 최종 상세는 각 구현 PR의 `api_spec.md`, `data_model.md`, component/test 문서에서 고정한다.

이 문서는 내부 planning inventory의 전체 내용을 복사한 것이 아니다. MBA-105 구현에 필요한 정책 기본값, 보안 gate, error matrix, recovery 기준만 공식 docs 구조에 맞게 승격한다.

## 범위

MBA-105에서 baseline으로 삼는 범위:

- document/source item 1개 = document-level Knowledge Base.
- Legacy one-to-many schema는 비파괴 호환을 위해 읽을 수 있지만 신규 manual Document 등록은 빈 active KB의 최초 row 하나만 허용한다. 독립 source item을 추가할 때는 별도 KB와 Knowledge Collection을 사용한다.
- Knowledge Collection = grouping, routing, UX, operations 단위.
- Workflow에는 별도 RAG node를 만들지 않고 LLM node의 RAG option/runtime path로 연결한다.
- MVP Workflow RAG는 execution subject가 없으면 anonymous public-only로 낮추고, active public collection에 연결된 active KB만 검색한다. Source-managed KB는 valid source/connector public exposure approval도 필요하다.
- Source-managed KB retrieval은 mbased KB `use`와 fresh source ACL/requester authorization 두 gate를 모두 통과한다.
- Source ACL facts는 authorization provenance와 freshness evidence이며 KB `use` 자체가 아니다.
- Chunk content, embedding input, retrieval-visible artifact, prompt context는 redacted canonical text를 기본 원천으로 사용한다.
- Router, Builder, runtime은 Knowledge Permission Helper가 만든 authorized safe candidate set만 소비한다.
- MCP/API source connector는 LLM 자유 tool-use가 아니라 Knowledge Source Connector 뒤의 allowlist adapter로 사용한다.

MBA-105에서 구현하지 않는 범위:

- G1 승인 없는 production destructive reset/split/backfill.
- Raw artifact opt-in storage와 raw/compliance permission enum finalization.
- Code-bearing Knowledge Skill.
- LLM-assisted query rewrite.
- Break-glass source ACL bypass.
- Private KB access for non-interactive runs through service account, assigned operator, or deployment preflight.
- Global/main Agent retrieval path.
- Platform-wide Workflow HTTP/GitHub/Mail egress guard.
- Workflow Playground/canvas UX, draft skill authoring, submit-for-review, publish/deprecate UI.

## 운영 기본값

아래 값은 MBA-105의 초기 baseline이다. 운영 설정으로 조정 가능해야 하며 제품의 고정 계약이 아니다. 운영 배포 전 부하 테스트와 담당자 검토가 필요하다.

| 항목 | 기준값 |
| --- | --- |
| ACL freshness TTL | 기본 24h, 민감하거나 변경이 잦은 source는 1h-6h |
| ACL revocation propagation | 민감 source 5분 목표, 일반 source 15분 목표 |
| Content sync cadence | source별 6h-24h, webhook/incremental 가능 시 우선 |
| ACL-only sync cadence | 15m-1h, content sync와 분리 |
| Chunk size / overlap | child chunk 800-1,200 tokens, overlap 10-20% |
| Parent/child hierarchy | parent 2,000-4,000 tokens, child 500-1,000 tokens |
| Candidate caps | `max_candidate_kbs=5000`, `max_route_collections=20`, `max_retrieval_kbs=20`, `max_chunks_per_kb=8`, `max_total_chunks=50`. Collection/KB candidate cap은 임의 row를 먼저 자른 뒤 authorization하는 방식이 아니라, route/use/source ACL helper를 통과한 authorized subset에 적용한다 |
| Fanout | 단일 filtered vector/keyword query 우선. Workflow의 per-KB fallback은 invocation당 동시 검색, 프로세스 전체 native blocking-I/O data worker와 제출 admission을 각각 최대 5개로 제한하고 authorized candidate ordinal을 보존한다. Executor는 greenlet 생성 전에 invocation/process slot을 예약하고 포화 시 추가 대기열을 만들지 않은 채 기존 partial-failure 정책으로 닫는다. Session factory/connection checkout도 별도 프로세스 전체 최대 5개의 bounded native acquisition worker로 제한하며 deadline 뒤 늦은 session은 획득 thread가 정리한다. DB cancel은 별도 bounded native control worker에서 처리한다 |
| Retrieval timeout | Workflow per-KB fallback은 호출당 10s, 최초 제출 전부터 caller aggregate 30s, 마지막 1s cleanup reserve. Queue 대기와 cleanup 대기를 aggregate에 포함한다. 각 DB worker는 모든 SQL 직전에 동일한 task 절대 deadline과 cancellation을 재검증하고 statement timeout을 남은 budget으로 축소한다. Non-DB 작업이 cancellation에 협조하지 않아도 caller deadline을 연장하지 않고 late result를 폐기한다 |
| Runtime authorization batch | `check_access_batch` 50-200 source item 후보. Batch 미지원 source는 bounded single check fallback만 허용 |
| Runtime authorization fallback | per-source concurrency 3-5, per-call timeout 3-5s, aggregate timeout 10-20s 후보. Timeout/unknown은 private evidence fail-closed |
| Runtime access cache | `allowed`/`denied` 1-5m, `unknown`/timeout 30-60s 후보. Source ACL/mapping epoch 변경 시 즉시 무효화 |
| Content safety scan | 지원 file type/content type allowlist, archive cap, active content detection, scan timeout/unknown fail-closed 후보. Binary/Office/archive는 scan hook 없으면 production indexing-visible 대상에서 제외 |
| Recovery scanner | local/non-prod 5m, production 후보 5-15m |
| Tombstone retention | 30-90 days, legal hold가 있으면 연장 가능 |
| Sync event retention | safe metadata only, 30-90 days 후보 |
| Raw artifact retention | 기본 off. Opt-in 시 30-90 days 및 expiry 후 24-72h purge SLA |
| Query rewrite 기본값 | `off`; MBA-105 runtime은 deterministic/template rewrite opt-in만 구현 |
| Evidence sufficiency 기본값 | `minimum_evidence`; legal/policy/compliance/high-risk flow는 `strict_citation` 후보 |

## ADR-0014 gate 대체 기준

ADR-0014는 목표 구조와 gate 목록을 정의한다. ADR-0017은 MBA-105 범위에서 그중 일부 gate에 대해 임시 구현 baseline을 제공한다. ADR-0017이 값을 정한 항목은 MBA-105에서는 닫힌 것으로 본다. 단, production destructive migration, raw artifact opt-in, code-bearing skill, global/main Agent retrieval path, platform-wide Workflow egress guard는 여전히 별도 승인 대상이다. ADR-0014의 placeholder 이름이나 열린 선택지는 구현 schema로 직접 옮기지 않는다.

| ADR-0014 gate | MBA-105 구현 기준 |
| --- | --- |
| Source ACL materialization | Source ACL fact/provenance는 KB `use` grant가 아니다. Source policy 기반 KB `use` provisioning은 `source_policy_kb_use_grants` table에만 materialize한다. |
| Collection permission storage | `team_knowledge_collection_permissions` / `user_knowledge_collection_permissions`와 `permission_action` additive allow row를 사용한다. |
| Anonymous public-only Workflow RAG | Execution subject가 없으면 owner/user_id fallback 없이 active public collection의 active KB만 candidate로 사용한다. Public collection은 `safe_metadata["visibility"] == "public"`으로 판정한다. Source-managed KB는 valid source/connector public exposure approval도 통과해야 한다. |
| Active version finalization | Active pointer swap, previous version `superseded`, processed state commit, cleanup/finalization outbox insert를 같은 DB transaction에서 수행한다. |
| Resource hiding API matrix | 이 문서의 safe hidden/no-result/partial-result matrix를 따른다. Hidden path의 external reason은 `resource.hidden`으로 일반화한다. |
| Retry/dead-letter vocabulary | Outbox/recovery row는 status, lease/fencing, attempt, next retry, dead-letter, re-drive contract를 가진다. |
| MCP incremental sync boundary | MCP/API source connector는 approved operation allowlist만 호출한다. Runtime source authorization은 batch 우선 primitive와 bounded fallback을 사용하고, Live-linked search는 requester-scoped 또는 opaque-ref-only flow만 허용한다. |
| Content safety and parser isolation | External source content는 egress guard 이후에도 untrusted다. File type allowlist, active content 차단, archive cap, parser sandbox, scan timeout/unknown fail-closed를 redacted canonical text 생성 전에 적용한다. |
| Knowledge delegated administration | MBA-231은 [ADR-0034](../../decisions/ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md)의 KB object/property action, owner migration, Team/User domain permission, self-escalation 차단과 transaction-bound audit를 사용한다. |

## Permission Helper 기준

Permission Helper는 DB permission row나 source ACL row를 router, Builder, LLM planner, controller에 직접 노출하지 않는다. 모든 후보 생성은 helper result를 통해 수행한다.

필수 gate:

- active organization과 membership 상태.
- 목록 화면에 필요한 collection `read`.
- auto collection candidate scope에 필요한 collection `route`.
- content retrieval에 필요한 KB `use`.
- source-managed KB의 fresh requester source authorization.
- prompt construction, answer delta, citation preview, trace/audit summary 생성 전 final evidence policy.

관리 API는 같은 helper/service 경계에서 KB `read`, `write`, `content_read`,
`manage`와 organization-scoped Knowledge domain action을 평가한다. Domain action은
retrieval helper의 KB `use`나 Collection `route` 결과에 합산하지 않는다.

필수 helper output:

| 항목 | 의미 |
| --- | --- |
| `allowed` | retrieval/candidate inclusion 가능 여부 |
| `resource_visibility` | `visible`, `hidden`, `resource_hidden`, `admin_visible` 같은 safe visibility class |
| `effective_auth_state` | normalized mbased permission state. Source ACL state와 섞지 않는다 |
| `source_acl_state` | source ACL freshness/mapping 상태. `fresh`, `stale`, `unmapped`, `ambiguous`, `unverified`, `revoked`, `not_source_managed` |
| `requester_source_authorization` | fresh source ACL 기준 requester 접근 판정. `allowed`, `denied`, `unknown`, `not_applicable` |
| `freshness_epoch` | ACL-only change, revocation, mapping change, source policy change 시 candidate cache invalidation에 사용 |
| `reason_code` | sanitized reason family |
| `safe_metadata` | display-policy-approved label/category/source type 같은 safe candidate metadata |

Collection permission storage의 MBA-105 baseline table은 `team_knowledge_collection_permissions`와 `user_knowledge_collection_permissions`다. 구현 PR에서 migration을 작성할 때도 다음 원칙을 지켜야 한다.

- Collection permission은 child KB content retrieval 권한을 상속하지 않는다.
- `collection.route`는 router scope 권한이지 KB `use`가 아니다.
- `safe_metadata["visibility"] == "public"`은 MVP anonymous public-only runtime의 candidate inclusion flag다. 누락 또는 다른 값은 private로 취급한다. 이 값은 authenticated subject 기반 retrieval의 KB `use` 권한을 부여하지 않는다.
- Collection permission row는 기존 `auth_state` 계층으로 추론하지 않고 `permission_action` 값(`read`, `route`, `manage`, `sync`)을 저장하는 additive allow 모델을 기본으로 한다.
- Unique key는 `(organization_id, collection_id, subject_type, subject_id, permission_action)` 또는 team/user 전용 table의 동등한 key를 사용한다.
- Linking/unlinking에는 collection manage와 대상 KB manage가 모두 필요하다.
- Router와 controller는 collection permission row를 직접 조합하지 않는다.

`source_policy_kb_use_grants`는 organization-approved connector/source policy가 provision한 KB `use` allow row를 저장한다. Manual team/user/admin grant와 다른 table로 두어 policy expiry/revocation 시 source-policy-provisioned grant만 inactive 처리할 수 있어야 한다. 필수 정보는 다음과 같다.

- `organization_id`, `knowledge_base_id`, subject type/id.
- `source_policy_id`, `provisioned_by`, `expires_at`.
- protected `source_identity_id`.
- `revocation_behavior`, active/inactive 상태, audit-safe reason.
- freshness epoch 또는 policy epoch.

Source ACL authorization provenance 저장소는 KB `use` grant처럼 읽히지 않는 이름을 사용한다. 구현 PR의 최종 table 이름과 무관하게 필수 정보는 다음과 같다.

- `organization_id`, `knowledge_base_id`, protected `source_identity_id`.
- requester subject safe reference 또는 mapped principal reference.
- source ACL state, freshness expiry, freshness epoch.
- requester source authorization result. `denied`는 freshness 상태가 아니라 requester가 원천 source에서 접근 권한이 없다는 판정 결과다.
- source permission/provenance metadata는 safe enum으로만 저장.
- active/inactive 상태와 revocation marker.
- Source principal/source id safe ref를 저장하는 경우 HMAC key version.

Source ACL fact만으로는 KB `use`가 충족되지 않는다. Auto-ingested KB는 admin/team/user grant 또는 `source_policy_kb_use_grants` allow row가 존재할 때만 retrieval 후보가 된다.

`subject_type="organization"` source-policy grant는 해당 organization의 active member에게만 적용된다. 단순히 `organization_id`가 일치한다는 이유로 비회원, removed, suspended, invited user에게 KB `use` 후보를 부여하지 않는다. Legacy owner/manager compatibility는 organization manager override 경로로만 취급하고 source-policy grant 소비 조건으로 확장하지 않는다.

Policy expiry, connector revocation, source ACL revocation, policy disable은 source-policy-provisioned grant만 inactive 처리하고 `freshness_epoch`와 candidate cache를 갱신해야 한다. Manual team/user/admin grant row는 저장상 유지될 수 있지만, source-managed KB에서는 fresh source ACL/requester authorization gate가 fail-closed이면 retrieval 후보가 될 수 없다.

## MCP/API source connector 기준

MCP 서버는 Knowledge Source Connector 뒤의 제한된 data access provider다. LLM runtime이 MCP tool을 자유 선택해 source raw data를 prompt/context에 직접 넣는 구조는 구현하지 않는다.

허용 operation baseline:

- `list_sources`
- `list_changed_items(cursor)`
- `fetch_item_content(source_item_ref)`
- `list_acl_changes(acl_cursor)`
- `check_access_batch(subject_ref, source_item_refs[])`
- bounded single `check_access(subject_ref, source_item_ref)`
- `get_tombstones(cursor)`
- 명시적 capture signal source에서만 `list_capture_events(cursor)` 또는 webhook event normalization

새 operation을 추가하려면 목적, source별 필요성, input/output field allowlist, timeout/page/size/retry/rate-limit cap, raw field 저장 금지, audit/trace safe reason code, negative test를 먼저 정의한다.

MCP/API adapter input에는 protected source ref, safe connector/source type, cursor/page token/event id, Nodease subject safe ref 또는 mapped source subject ref, operation별 allowlist parameter만 전달한다. Raw credential, token, API key, raw private URL/path/title, user-facing prompt 전문, LLM completion, raw source payload를 uncontrolled query parameter로 전달하지 않는다.

MCP/API adapter output은 Knowledge가 소비할 safe normalized shape로 변환한다. Protected source identity, source type, safe display label, content updated timestamp, tombstone state, source authorization state, requester authorization result, freshness epoch, redacted canonical content 또는 redaction 전 ephemeral content handle만 허용 후보로 둔다. Raw source id/url/path/title, raw principal/email, raw ACL row, raw tool error, token, credential, secret, hidden resource name/id, exact denied count는 다음 계층으로 전달하지 않는다.

Redaction 전 ephemeral content handle은 redaction pipeline 내부에서만 재해석할 수 있는 process/run-scoped short TTL handle이어야 한다. Handle value나 raw content는 durable DB, retry/dead-letter payload, audit, trace, log, user-facing response에 저장하지 않는다.

## Content safety 및 parser isolation 기준

OutboundEgressGuard는 네트워크와 protocol access 위험을 줄이는 gate이며, 가져온 content 자체를 신뢰 가능하게 만들지 않는다. Source content는 redacted canonical text로 변환되기 전에 content safety gate를 통과해야 한다.

기본 기준:

- 지원 file type/content type allowlist를 source policy별로 둔다. Allowlist 밖 file은 indexing-visible artifact를 만들지 않고 remediation 또는 safe unsupported 상태로 둔다.
- Parser/extractor는 macro, script, embedded object, external reference, executable payload를 실행하지 않는 least-privilege 또는 sandboxed worker에서 수행한다.
- Office, PDF, HTML, archive, rich document parser는 network access와 filesystem write 범위를 제한한다. Parser가 외부 URL을 따라가거나 document 내부 script를 실행하면 안 된다.
- ADR-0070 Target enforcement의 기본 parser는 network-disabled local isolated parser다. Current
  `llamaparse` credential의 Organization/`use` 검증은 raw-content egress 승인이 아니다. External parser는
  explicit Organization opt-in과 source-managed source 또는 manual KB scope opt-in, exact Raw Parser
  Egress Approval Revision, server-owned `knowledge.parser.external_approved` profile/credential capability와
  ADR-0067 public-address guarded transport 및 no-log/no-durable-payload readiness가 구현되기
  전 비활성화하고 raw upload 전에 `knowledge.raw_parser_egress_unavailable`로 fail-closed한다. 승인된
  parser output도 local hard baseline을 우회하지 않는다. Private raw parser는 별도 Accepted ADR과
  dedicated isolation profile 전까지 지원하지 않는다. 현재 FileProcessor는 이 판정을 source fetch,
  credential lookup과 SDK 생성 전에 수행하고, preview와 durable ingestion에 같은 safe reason을 전달하며
  local parser fallback으로 외부 parser 실패를 숨기지 않는다.
- Archive는 nested depth, expanded size, contained file count, nested archive count cap을 적용한다. Cap 초과, archive bomb, executable/script/macro-enabled child file은 기본 fail-closed 또는 quarantine/remediation이다.
- Malware/content scan hook은 provider-neutral interface로 둔다. Hook이 없거나 scan result가 `unknown`, `timeout`, `error`이면 high-risk binary/Office/archive는 ready/indexing-visible 상태로 진행하지 않는다.
- Scan pass는 source ACL, KB `use`, redaction, prompt-injection guard를 대체하지 않는다.
- Content safety failure는 raw file bytes, active content marker, parser exception raw detail을 durable DB, retry/dead-letter payload, audit, trace, log, user-facing response에 저장하지 않는다. Safe reason code, scanner/parser family, retryability, remediation action만 저장한다.

## Source mode 기준

| Mode | 저장/검색 기준 |
| --- | --- |
| `indexed_cache` | MVP 기본 mode다. Redacted canonical chunk, embedding, safe metadata, protected source identity를 저장하고, runtime source authorization과 final evidence policy를 통과한 evidence만 prompt/citation에 사용한다. |
| `live_linked` | 내부 chunk/embedding이 없으므로 일반 vector retrieval 후보가 아니다. Requester-scoped source-side search API가 있거나, source-side search 결과가 opaque source ref만 반환되고 runtime source authorization 이후에만 metadata가 노출될 때만 검색 후보가 된다. Broad service account search 결과의 title/snippet/count/score를 authorization 전에 받는 구조는 금지한다. |
| `archived_copy` | 기본 비활성이다. Protected raw artifact는 opt-in raw/compliance policy, encryption, retention/legal hold/purge, access audit, fresh source ACL gate가 닫힌 뒤에만 보존할 수 있다. Raw artifact는 RAG, embedding, prompt, answer stream, citation summary input으로 사용하지 않는다. |

## Runtime source authorization 기준

Runtime source authorization은 final evidence gate 전에 끝나야 한다.

- `check_access_batch(subject_ref, source_item_refs[])`를 우선 사용한다.
- Batch 미지원 source는 bounded concurrency, per-call timeout, aggregate timeout을 적용한 single `check_access` fallback만 허용한다.
- Runtime authorization primitive 자체가 없는 source는 private source-managed KB retrieval을 fail-closed 처리한다.
- Source subject mapping 상태 `unmapped`, `ambiguous`, `stale` revalidate 실패, `revoked`는 private source-managed retrieval에서 fail-closed다.
- Batch 일부 item timeout이나 실패는 해당 item만 `unknown` 또는 safe operational failure로 낮춘다.
- Runtime access cache key는 organization, connector, protected source identity, source item 또는 document version, execution subject, mapping epoch, source ACL freshness epoch, operation을 포함한다. Subject-level `allowed`를 다른 source item에 재사용하지 않는다.
- Cached `allowed`는 KB `use`, source ACL freshness, final evidence policy를 대체하지 않는다.
- Raw source id/title/path/url/principal, raw ACL row, raw tool response/error는 cache key/value, audit, trace, log에 저장하지 않는다.

## Public exposure approval 기준

`KnowledgeCollection.safe_metadata["visibility"] == "public"`은 anonymous public-only candidate inclusion flag일 뿐 source-managed KB public exposure approval이 아니다.

Source-managed KB를 anonymous public-only 후보에 포함하려면 다음을 모두 만족해야 한다.

- Collection active 상태와 collection public visibility approval.
- KB active 상태와 public collection link.
- Source/connector public exposure approval.
- Public exposure `approval_scope`와 target field consistency.
- Final evidence policy와 redaction/prompt-injection guard.

`approval_scope`는 `connector`, `source_identity`, `collection`, `knowledge_base` 같은 bounded enum으로 둔다. `approval_scope=connector`는 broad approval이므로 organization manager approval, explicit acknowledgement, `expires_at`, reverification cadence, revocation behavior가 모두 필요하다. `approval_scope=source_identity`는 `source_identity_id`, `approval_scope=collection`은 `collection_id`, `approval_scope=knowledge_base`는 `knowledge_base_id`가 있어야 한다. Target field가 없거나 scope와 target field가 맞지 않는 row는 invalid policy로 보고 public-only 후보에서 제외한다.

Public exposure expiry, connector revocation, source identity tombstone, source ACL public status 변경, redaction/display policy failure는 public-only candidate cache invalidation을 유발해야 한다. Source public ACL만으로 organization-wide read/use 또는 anonymous public exposure를 만들지 않는다.

## Source Identity와 표시 정책

Protected source identity는 user-facing resource가 아니다. Raw source id, URL, path, title, principal, raw ACL fact, connector exception string은 router, Builder, audit, trace, log, user-facing summary로 전달하지 않는다.

기준은 다음과 같다.

- Source identity는 keyed HMAC-SHA256 safe ref를 가진 protected table/ref에 저장한다.
- `hmac_key_version`을 저장하고 rotation 중 dual-read/backfill을 지원한다.
- Tombstone matching과 revocation matching에는 protected ref를 사용한다.
- Source-derived title/path/url/description은 redaction, 길이 제한, display policy 승인 이후에만 표시할 수 있다.
- Durable audit/trace/log summary 저장 허용 범위는 UI display 허용 범위보다 더 엄격하다.

## Active Version Finalization 기준

Target ingestion은 partial artifact를 retrieval-visible하게 만들면 안 된다. Active version만 기본 retrieval-visible version이다.

기준 절차는 다음과 같다.

1. Source item 또는 document-level KB 단위 owner-token lock, fencing token, 또는 동등한 advisory lock을 획득한다.
2. 승인된 egress guard와 protocol adapter를 통해 fetch/probe/sync를 수행한다.
3. Source content safety gate가 file type allowlist, archive cap, active content 차단, parser isolation, scan hook 결과를 평가한다.
4. Content safety gate를 통과한 source content만 redacted canonical text와 safe metadata로 정규화한다.
5. `document_versions` row를 `staging` 또는 `indexing` 상태로 만든다.
6. Chunk, embedding, keyword/vector index artifact를 non-visible namespace 또는 non-visible status로 만든다.
7. 모든 artifact와 metadata가 준비됐는지 검증한다.
8. 짧은 DB transaction에서 `document_versions.status=ready`를 설정하고 `knowledge_bases.active_document_version_id`를 교체하며 이전 active version을 `superseded`로 표시하고, `content_hash`, chunking fingerprint, embedding model reference, processing policy version, cleanup/finalization idempotent outbox event를 함께 commit한다.
9. Transaction commit 이후 worker가 outbox event를 실행하거나 background worker가 가져가도록 둔다.
10. Recovery scanner는 만료된 finalizing run, orphan index namespace, 실패한 outbox event, stale cleanup task를 복구한다.

불변 조건은 다음과 같다.

- `active` document version status 단독으로 active source of truth를 표현하지 않는다. `ready` status와 `knowledge_bases.active_document_version_id` pointer를 함께 active retrieval indicator로 사용한다.
- Finalization이 성공하기 전까지 기존 active version은 계속 retrieval-visible 상태로 유지한다.
- MBA-105 legacy compatibility에서는 `active_document_version_id`가 아직 없는 KB에 한해 `document_chunks.document_version_id IS NULL` chunk를 retrieval-visible로 둘 수 있다. KB에 active pointer가 생긴 뒤에는 `ready` active document version chunk만 retrieval-visible하다.
- ADR-0070 Target enforcement 뒤 위 legacy compatibility는 privacy-compliant active pointer를 한 번도 확정하지 않은 document가 frozen `legacy_unverified` migration wave membership, 아직 retire되지 않은 eligibility, wave의 frozen platform/Organization validity epoch와 current 두 epoch equality, code-owned `privacy_legacy_grace_v1 = 30 * 24 hours` 상한과 authoritative DB-time half-open cutoff를 retrieval prefilter와 final evidence gate 모두에서 통과한 경우로 제한한다. Security-invalidating epoch 전진은 item projection/cleanup과 별개로 legacy를 즉시 제외한다. Compliant pointer finalization은 legacy eligibility를 같은 transaction에서 비가역적으로 retire한다. 이후 active manifest가 stale/invalid가 되거나 cleanup이 남아 있어도 legacy로 fallback하지 않는다. Allowlist/wave 부재, stale validity epoch, unsupported grace contract, cutoff equality/경과와 stale cache/vector result는 legacy visibility를 연장하지 않는다. Privacy-gated active-ready artifact도 manifest의 global platform 및 same-Organization validity epoch가 current 두 epoch와 모두 같을 때만 retrieval-visible하며 security-invalidating epoch 전진은 pointer와 별개로 즉시 제외한다.
- Legacy `documents` row가 남아 있는 전환기 retrieval 구현은 vector, keyword, hierarchy search 모두에서 `documents.status='completed'` 문서에 속한 chunk만 retrieval-visible로 취급한다. `pending`, `processing`, `failed`, `deleted` 또는 동등한 미완료/실패 상태의 chunk는 active pointer 조건을 만족하더라도 evidence 후보가 될 수 없다.
- Pre-finalized chunk, vector, keyword index artifact는 retrieval-visible하지 않다.
- External index 성공 후 DB finalize가 실패하면 기존 active version을 유지하고 cleanup을 queue에 넣는다.
- DB finalize 성공 후 cleanup이 실패하면 같은 transaction에서 기록된 outbox event를 기준으로 새 active version을 유지하고 cleanup을 retry한다.
- Vector/keyword retrieval-visible artifact는 `organization_id + knowledge_base_id + active_document_version_id` filter 또는 동등한 tenant-scoped version namespace를 사용한다.

`knowledge_ingestion_outbox`와 recovery scanner가 가져야 하는 최소 상태는 다음과 같다.

| 항목 | 기준 |
| --- | --- |
| `status` | `pending`, `leased`, `succeeded`, `retry_scheduled`, `dead_lettered`, `cancelled` 후보 |
| Lease/fencing | owner token, fencing token, lease expiry를 저장하고 만료 lease는 recovery scanner가 회수한다 |
| Retry | `attempt_count`, `max_attempts`, `next_retry_at`, backoff policy, retryable flag, safe reason code를 저장한다 |
| Dead-letter | `dead_lettered_at`, final safe reason, remediation/re-drive 가능 여부를 저장한다 |
| Re-drive | 권한 있는 운영자가 safe remediation action으로 재시도할 수 있어야 하며 원본 raw payload나 secret은 노출하지 않는다 |

## Resource Hiding 및 결과 matrix

아래 matrix는 MBA-105 구현 baseline이다. API test에 그대로 반영해야 한다. 이후 API/Gateway 결정이 더 엄격하면 해당 결정이 이 matrix를 대체한다.

| 상황 | 기본 응답 | 답변/run 동작 | 감사/trace 동작 |
| --- | --- | --- | --- |
| Explicit KB가 없거나 organization mismatch/scope 밖인 경우 | JSON/pre-stream: `404 resource.hidden`; stream 시작 후: `resource.hidden` SSE terminal error | Answer run, citation, `rag.retrieve` success를 만들지 않는다 | request/org/actor만 포함한 request-scoped security audit는 허용. target KB id/name은 금지 |
| Deleted/archived hidden KB 또는 hidden document version | Explicit mode: `404 resource.hidden`; auto mode: hidden candidate 제외 후 필요 시 safe no-result | Admin/preflight context가 존재를 명시적으로 볼 수 있는 경우가 아니면 answer run을 만들지 않는다 | Hidden id/name/count 금지. Safe reason class만 허용 |
| Requester source authorization denied 또는 source ACL stale/unmapped/ambiguous/unverified/revoked가 존재 추론을 만들 수 있는 경우 | Explicit mode: `404 resource.hidden`; auto mode: candidate 제외 후 safe no-result 또는 insufficient evidence | Evidence를 만들지 않고 hidden resource에 대한 success retrieval lifecycle을 남기지 않는다 | External reason은 `resource.hidden`으로 일반화한다. 세부 source authorization/source ACL reason은 authorized admin/remediation context 또는 내부 safe audit allowlist에서만 허용한다 |
| Same-scope visible KB의 `use` 권한 부족 | 이미 visibility가 허용된 resource면 `403 permission.denied`; 그렇지 않으면 `404 resource.hidden` | Visibility 이후 execution run이 생성됐다면 blocked, 그 전이면 no run | Authorized target이면 `permission.denied` safe target 허용. 아니면 request-scoped hidden audit만 허용 |
| Credential/model use 권한 부족 | Actor에게 보이는 credential/model safe id에 한해 `403 permission.denied` | Visibility 이후 execution run이 있으면 blocked/failed 중 lifecycle policy에 맞춰 terminal 처리 | KB/source ACL denial과 분리 |
| 허용된 candidate 이후 policy/final evidence block | JSON/pre-stream: safe `403 policy.blocked`; stream 시작 후: `policy.blocked` SSE terminal error | Run이 있으면 blocked/failed로 닫는다. Blocked evidence에서 answer delta/citation preview를 만들지 않는다 | `policy.block` safe metadata만 허용. raw evidence 금지 |
| Auto mode에서 authorized candidate가 없는 경우 | `200 no_result` with `evidence_sufficient=false` | Hidden candidate ids/counts 금지. Runtime node는 no-result로 완료하거나 failure policy를 따른다 | Safe no-result summary만 허용 |
| Authorized retrieval 결과가 0건인 경우 | `200 no_result` with `insufficiency_reason=no_evidence` | 추측 답변 생성을 위해 LLM을 호출하지 않는다 | Safe retrieval summary만 허용. hidden resource implication 금지 |
| Evidence score가 낮거나 citation이 부족한 경우 | `200 insufficient_evidence` | Unsupported answer를 생성하지 않는다 | `low_score`, `insufficient_citation` 같은 safe reason class 저장 |
| 일부 authorized KB retrieval만 operational failure인 경우 | 남은 evidence가 gate와 sufficiency를 통과할 때만 safe partial result 허용 | `partial_result=true`; failed count는 bucket 처리 | Safe reason summary와 retryability만 허용. exact failed KB id 금지 |
| 모든 authorized retrieval이 operational failure인 경우 | Automation은 terminal operational error 기본값. Interactive path는 workflow/user-facing policy가 허용한 경우에만 configured safe no-result | Fabricated answer 금지 | Safe operational reason과 retryability만 허용 |
| Stream 시작 후 오류 | Semantic status/reason을 포함한 SSE terminal error event | Stream 시작 후에는 HTTP status를 바꿀 수 없다 | JSON path와 동일한 safe metadata 규칙 적용 |

Reason code family는 안정적이고 sanitized된 값이어야 한다.

- `resource.hidden`
- `permission.denied`
- `source_authorization.denied`
- `source_acl.stale`
- `source_acl.unmapped`
- `source_acl.ambiguous`
- `source_acl.unverified`
- `source_acl.revoked`
- `policy.blocked`
- `evidence.no_evidence`
- `evidence.low_score`
- `evidence.insufficient_citation`
- `retrieval.partial_operational`
- `retrieval.unavailable`
- `egress.private_target`
- `adapter.sql_not_allowed`
- `adapter.ssh_command_not_allowed`
- `adapter.object_listing_too_broad`

Hidden/resource-hidden path의 external `reason_code`는 항상 `resource.hidden` 또는 safe no-result/insufficient-evidence reason으로 일반화한다. `source_authorization.denied`, `source_acl.stale`, `source_acl.unmapped`, `source_acl.ambiguous`, `source_acl.unverified`, `source_acl.revoked` 같은 세부 reason은 이미 존재가 authorized context에서 보이는 resource, admin/remediation context, 또는 내부 safe audit/trace allowlist에서만 사용할 수 있다.

## Retrieval 및 ranking 기준

- Auto collection mode는 route-allowed collection에서 시작해 helper-authorized KB를 만들고, 그 이후 router-selected KB를 선택한다.
- Explicit KB mode는 collection route를 생략할 수 있지만 KB use, source ACL, metadata filter, hierarchy mode, final evidence policy는 생략할 수 없다.
- Source-of-Truth Tier는 authorized evidence 안에서만 ranking, tie-break, conflict-resolution hint로 사용한다.
- Source tier는 retrieval/citation에 쓰는 document version 또는 canonical metadata에 둔다. Chunk metadata에는 ranking 목적으로 denormalize할 수 있다.
- Candidate source tier enum은 `legal_regulation`, `contract`, `company_policy`, `adr_decision`, `official_documentation`, `semantic_definition`, `operational_runbook`, `curated_query_corpus`, `conversation_or_thread`로 시작할 수 있다. 최종 enum은 Legal/Compliance review를 거쳐 확정한다.
- Legal/regulatory evidence는 strict use 전에 jurisdiction, effective date, version, review-required state를 가져야 한다.
- Query rewrite 기본값은 `off`다. MBA-105 runtime은 deterministic/template rewrite를 opt-in으로 구현하고, LLM-assisted rewrite는 LLMOps/cost/security gate가 닫히기 전까지 범위 밖이다.
- Raw rewritten query는 raw prompt와 같은 민감 입력으로 보고 durable audit, trace, usage, cache key, summary에 저장하지 않는다.
- CrossEncoder rerank는 기본값 `off`다. Worker image에서 reranker dependency를 포함하려면 `INSTALL_RAG_RERANKER=true` build arg를 명시하고, runtime에서 `RAG_CROSS_ENCODER_RERANK_ENABLED=true`를 설정해야 한다. 프로세스 cache 초기화는 native worker 사이에서 직렬화하고 완성된 model만 게시한다. Dependency가 없거나 model load/predict가 실패하면 retrieval은 원래 candidate 순서로 fallback하며, 이 fallback은 operational error가 아니라 degraded ranking path로 기록한다. 기본 local/demo/production path는 cold start와 image size를 피하기 위해 reranker를 로드하지 않는다.

Slack/meeting source item의 effective ACL baseline:

- Slack channel은 Knowledge Collection이다.
- Slack thread, huddle recap, canvas, bot-generated meeting summary, pinned-message group은 document-level KB다.
- Artifact-level ACL이 있으면 artifact ACL과 containing channel/workspace ACL의 교집합을 requester authorization으로 사용한다.
- Artifact ACL이 없거나 확인할 수 없으면 fail-closed 또는 remediation 상태로 둔다.
- Channel membership만으로 huddle recap, canvas, meeting summary를 자동 공개하지 않는다.
- DM, raw audio, raw transcript ingestion은 별도 opt-in policy 없이 구현하지 않는다.

## Workflow 및 Builder 기준

- Runtime RAG는 execution subject가 있으면 명시 `execution_subject` 기준으로 평가한다.
- Interactive run은 active membership validation을 통과한 request user를 사용할 수 있다.
- `internal_chatbot` 인증 run은 current user를 명시 `execution_subject`로 전달하고 해당 user의 KB `use`/source ACL을 재평가한다. 공개 `chatbot` run은 subject 없이 anonymous public-only다.
- Execution subject가 없으면 anonymous public-only로 낮추고, active public collection에 연결된 active KB만 검색한다. Source-managed KB는 valid source/connector public exposure approval도 통과해야 한다.
- Schedule, webhook, API trigger run의 private KB access는 deployment-approved service account 또는 명시적으로 지정된 operator가 필요하며 후속 기능이다. Subject가 없거나 inactive, removed, ambiguous 상태에서는 private KB retrieval을 수행하지 않는다.
- Workflow owner/builder permission을 runtime fallback으로 조용히 사용하지 않는다.
- MBA-176 Deployment preflight는 private RAG가 필요한 LLM node RAG option이 deployment type에서 파생한 runtime audience에게 사용 가능한지 탐지한다. Subject 없는 public/API/webhook/schedule/chatbot/MCP surface에서 private KB 후보가 있으면 활성 배포 create/toggle을 blocked로 닫고, source-managed public exposure approval primitive가 없으면 source-managed public 후보도 `source_public_exposure_required`로 blocked 처리한다. Service account와 assigned operator는 후속 기능이다.
- Builder candidate API는 safe label, route availability, runtime availability warning, safe reason, required action만 반환한다.
- Hidden KB id/name, exact denied count, raw source path/title/url, hidden source distribution은 Builder output에 포함하지 않는다.
- Runtime failure 기본값은 user-facing flow에서는 permission-safe no-result, automation에서는 명시 fallback branch가 없는 한 fail-node/fail-workflow다.

## Egress 및 protocol adapter 기준

모든 Knowledge/RAG source collection server-side fetch/probe/sync/preview path는 protocol adapter 동작 전에 shared outbound client/factory와 OutboundEgressGuard를 사용한다.

HTTP/URL guard 요구사항:

- 연결 전 DNS resolve를 수행하고 redirect 이후 IP를 다시 검증한다.
- Private, loopback, link-local, metadata IP, 차단된 internal network range를 거부한다.
- IDNA/punycode/CNAME/IPv4 obfuscation을 canonicalize한다.
- Scheme allowlist를 적용하고 HTTPS downgrade와 `verify=false`를 거부한다.
- Redirect count cap을 적용하고 redirect마다 sensitive header를 제거한다.
- Timeout, response size, content-type, decompression/zip bomb cap을 적용한다.
- Custom HTTP client 또는 raw socket으로 guard를 우회하지 않는다.

Protocol adapter 요구사항:

- DB adapter는 arbitrary SQL을 기본 금지한다. 승인된 read-only probe/query builder와 schema, row, time cap을 사용한다.
- SSH adapter는 별도 ADR이 sandboxed command behavior를 승인하기 전까지 command execution을 금지한다.
- Object storage adapter는 scoped bucket/prefix, object count cap, object size cap을 사용하고 broad recursive listing은 기본 금지한다.
- SaaS adapter는 least-privilege scope, pagination cap, safe metadata sanitizer를 적용하고, source가 지원하면 source ACL sync를 수행한다.

## Skill 및 evaluation 기준

- Knowledge Skill은 Builder 단계 LLM node RAG option 구성을 위한 provider-neutral 절차/context/routing metadata다.
- Skill은 permission source, source of truth, evidence가 아니다.
- 게시된 skill은 authorization, display policy, freshness, eval gate를 모두 통과한 뒤에만 노출한다.
- Draft skill은 명시적인 테스트 context에서만 허용할 수 있으며 운영 runtime에서 자동 선택하지 않는다.
- Skill metadata/body/resource에는 raw source content, raw title/path/url, raw principal, raw ACL fact, restricted document list, hidden KB id, credential value, raw prompt/completion, provider raw response를 포함하지 않는다.
- Skill cache key에는 skill version, freshness state, eval state, source version ref를 포함한다.
- Golden question fixture는 safe reference와 expected behavior만 저장하며 raw restricted content를 포함하지 않는다.

## Test phase 기준

| 단계 | 필수 검증 범위 |
| --- | --- |
| Phase 1 | egress negative path, protected source identity, basic sync, redaction, active version swap, transactional outbox insert, fencing token, recovery scanner smoke |
| Phase 2 | source ACL freshness, content cursor와 ACL/permission watermark 분리, Knowledge Permission Helper, KB use + source ACL two-gate, source-policy grant inactive lifecycle |
| Phase 3 | multi-KB cap, final evidence recheck, resource hiding matrix, retry/dead-letter transition, partial result behavior |
| Later | golden question, source tier tuning, LLM-assisted rewrite, advanced rerank |

Production readiness load test는 candidate cap, route collection cap, permission helper index, candidate cache, fanout concurrency, retrieval timeout, aggregate interactive timeout, recovery scanner cadence, trace/audit payload size, retry/dead-letter throughput, partial operational failure behavior를 포함해야 한다. 기능 회귀 테스트 통과만으로 이 성능 기준이 검증됐다고 보지 않는다.

초기 pass/fail baseline은 다음 값을 출발점으로 삼는다. 실제 배포 전 owner review가 더 엄격한 값을 승인할 수 있다.

| 항목 | 초기 acceptance 기준 |
| --- | --- |
| Candidate resolver | 5,000 KB 후보에서 p95 2s 이하, p99 5s 이하 |
| Interactive retrieval aggregate | 설정된 cap 안에서 30s hard timeout 이내 terminal result |
| Permission helper | per-KB 반복 query 없이 bulk evaluation 사용, 5,000 후보 p95 1s 이하 |
| Recovery scanner lag | orphan/finalizing/outbox event를 2회 scanner interval 이내 감지 |
| Trace/audit payload | 단일 event 16KB 이하, answer/run summary 64KB 이하 후보 |
| Retry/dead-letter | max attempt 도달 시 dead-letter 전이, re-drive 시 idempotency key로 중복 active pointer swap 금지 |
| Partial failure | 일부 authorized retrieval failure는 bucketed count와 safe reason만 남기고 hidden id/count 누출 없음 |

Workflow LLM node의 RAG retrieval trace payload는 redaction-safe chunk summary만 저장하고, 초기 구현에서는 최대 20개 chunk summary만 durable payload에 포함한다. 전체 검색 결과 수는 safe count로 남길 수 있지만, payload가 cap을 넘으면 `retrieved_chunk_summary_truncated=true`로 표시한다.

Workflow LLM node가 LLM provider 호출을 위해 redacted canonical text 기반의 authorized Knowledge context를 prompt에 포함하더라도, durable prompt trace payload는 Knowledge context body를 중복 저장하지 않는다. Prompt trace는 `[BEGIN KNOWLEDGE - UNTRUSTED]`와 같은 Knowledge context block을 redacted marker 또는 동등한 safe summary로 대체해야 하며, RAG evidence 저장소로 사용하지 않는다.

## Gate closure 전 구현 금지

- G1 data-preservation 승인 없이 destructive KB reset/split/backfill을 구현하지 않는다.
- Source ACL만으로 KB content retrieval을 허용하지 않는다.
- Organization-approved connector/source policy가 explicit KB permission grant를 만들기 전에는 connector-discovered source ACL을 mbased KB `use`로 해석하지 않는다.
- Manual KB grant가 source-managed ACL freshness/requester authorization을 우회하게 만들지 않는다.
- 하위 KB content access를 상속 부여하는 collection permission storage를 만들지 않는다.
- Pre-finalized document version, chunk, vector artifact를 retrieval-visible하게 만들지 않는다.
- Raw source content를 RAG, embedding, retrieval-visible, prompt, router, answer stream input으로 저장하거나 사용하지 않는다.
- Raw source title/path/url/principal, hidden KB id, exact denied count를 router, Builder, audit, trace, user-facing summary에 노출하지 않는다.
- Slack/meeting DM, raw audio, raw transcript ingestion은 기본 구현하지 않는다.
- 이 Knowledge policy와 별도의 global/main Agent retrieval path를 구현하지 않는다.
- Model, credential, cost, timeout, fallback, usage logging, security approval 없이 LLM-assisted query rewrite를 활성화하지 않는다.
- Sandbox, approval, egress, resource cap, audit ADR 없이 code-bearing Knowledge Skill을 활성화하지 않는다.
- Workflow owner/builder permission을 runtime RAG fallback으로 사용하지 않는다.
- Knowledge OutboundEgressGuard가 모든 Workflow HTTP/GitHub/Mail runtime node를 보호한다고 가정하지 않는다.
