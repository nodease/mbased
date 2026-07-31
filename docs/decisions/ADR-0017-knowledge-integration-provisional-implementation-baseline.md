# ADR-0017: Knowledge 통합 임시 구현 baseline

Status: Accepted

Related ADRs: [ADR-0006](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0015](ADR-0015-knowledge-skill-context-routing-boundary.md)

## Context

[ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)는 Knowledge Base를 document/source item 단위 atom으로 재정의하고 Knowledge Collection을 grouping/routing/ops 단위로 두는 목표 구조를 승인했다. 다만 source ACL materialization, active version finalization, resource hiding, query rewrite, evidence sufficiency, sync cadence, connector granularity 같은 항목은 구현 전 gate로 남아 있었다.

Knowledge/RAG 구현을 지연하지 않기 위해 MBA-105에서는 local planning 문서의 보수적 권장안을 임시 합의 baseline으로 채택한다. 이 ADR은 해당 권장안을 공식 docs에서 참조할 수 있는 구현 기준으로 요약한다. 후속 도메인 owner가 더 엄격하거나 구체적인 결정을 승인하면 이 ADR을 새 ADR로 대체하거나 갱신한다.

이 ADR은 목표 구조의 구현을 unblock하지만, 현재 코드가 이미 이 baseline을 충족한다는 뜻은 아니다.

기능별 구현자가 따라야 할 운영 기본값, resource hiding matrix, permission helper output, active version finalization, egress/protocol adapter baseline은 [Knowledge implementation baseline](../features/knowledge/implementation_baseline.md)에 모은다. 이 ADR은 정책 결정의 근거이고, 해당 feature 문서는 MBA-105 구현 체크리스트 역할을 한다.

ADR-0014는 목표 구조와 gate 목록을 정의한다. ADR-0017은 MBA-105 범위에서 그중 일부 gate에 대해 임시 구현 baseline을 제공한다. ADR-0017이 값을 정한 항목은 MBA-105에서는 닫힌 것으로 본다. 단, production destructive migration, raw artifact opt-in, code-bearing skill, global/main Agent retrieval path, platform-wide Workflow egress guard는 여전히 별도 승인 대상이다. ADR-0014가 placeholder 이름이나 열린 선택지로 언급한 항목은 MBA-105 범위에서 이 ADR의 값이 더 구체적인 후속 결정이다.

| ADR-0014 gate | MBA-105 provisional baseline |
| --- | --- |
| Source ACL materialization | Source ACL fact/provenance는 KB `use` grant가 아니다. Source policy 기반 KB `use` provisioning은 `source_policy_kb_use_grants` table에만 materialize한다. ADR-0014의 `source_knowledge_permission_grants` placeholder는 MBA-105 target schema로 구현하지 않는다. |
| Collection permission storage | `team_knowledge_collection_permissions` / `user_knowledge_collection_permissions`와 `permission_action`(`read`, `route`, `manage`, `sync`) additive allow row를 사용한다. |
| Active version finalization | Active pointer swap, previous version `superseded`, processed state commit, cleanup/finalization outbox insert를 같은 DB transaction에서 수행한다. |
| Resource hiding API matrix | 이 ADR과 Knowledge implementation baseline의 safe hidden/no-result/partial-result matrix를 따른다. Hidden path의 external reason은 `resource.hidden`으로 일반화한다. |
| Retry/dead-letter vocabulary | Outbox/recovery row는 status, lease/fencing, attempt, next retry, dead-letter, re-drive contract를 가진다. |

## Decision

MBA-105 Knowledge 통합 구현은 아래 임시 baseline을 따른다.

### Permission and Source ACL

- Source-managed KB retrieval은 항상 mbased KB `use`와 fresh source ACL/requester authorization 두 gate를 모두 통과해야 한다.
- Source ACL facts는 source authorization provenance와 freshness evidence다. Source ACL facts만으로 mbased KB `use`가 충족됐다고 해석하지 않는다.
- Auto-ingested KB는 다음 중 하나로 정상 mbased KB `use`가 부여된 경우에만 retrieval 후보가 된다.
  - admin/team/user가 명시적으로 KB `use` grant를 부여한다.
  - organization-approved connector/source policy가 명시 KB `use` grant를 provision한다.
- Source policy 기반 KB `use` provisioning은 manual `team_knowledge_permissions`/`user_knowledge_permissions` row와 섞지 않고 별도 `source_policy_kb_use_grants` table에 materialize한다. Permission helper는 이 row를 mbased KB `use` allow 후보로 합산하되, source ACL gate와 freshness gate를 별도로 통과시킨다.
- `source_policy_kb_use_grants`에는 source policy id, provisioned_by, subject type/id, expiry, protected source identity reference, revocation behavior, audit-safe reason, active/inactive state를 남긴다.
- Policy expiry, connector revocation, source ACL revocation, policy disable은 source-policy-provisioned grant만 inactive 처리하고 `freshness_epoch`/candidate cache를 갱신해야 한다. Manual team/user/admin grant row는 저장상 유지될 수 있지만, source-managed KB에서는 fresh source ACL/requester authorization gate가 fail-closed이면 retrieval 후보가 될 수 없다.
- Manual KB grant, manager/admin 권한, collection permission은 source-managed KB의 source ACL freshness/requester authorization을 우회하지 않는다. Break-glass 예외는 별도 ADR 전까지 없다.
- Public source ACL은 기본적으로 organization-wide read/use가 아니다. Connector policy와 organization policy가 모두 해당 public ACL을 명시 신뢰하고 approver, expiry, reverification, revocation, audit-safe metadata를 제공할 때만 KB `use` provisioning 후보가 된다.

### Sync, Versioning, and Recovery

- Content cursor와 ACL/permission watermark는 분리한다. Content cursor는 active version finalization 이후 전진하고, ACL watermark는 source ACL state와 candidate cache invalidation commit 이후 전진한다.
- Active version finalization은 redacted canonical text, chunks, embeddings, index namespace, metadata가 모두 준비된 뒤 `knowledge_bases.active_document_version_id`를 swap한다. `document_versions.status=ready`와 active pointer를 함께 사용하고 `active` status 단독에 의존하지 않는다.
- Pre-finalized chunks, vectors, keyword index artifacts는 retrieval-visible하지 않다.
- Active pointer swap, previous version `superseded` 표시, processed state commit, finalization/cleanup outbox insert는 같은 DB transaction 안에서 수행한다. External index/storage cleanup/finalization side effect는 idempotent outbox와 recovery scanner로 복구한다.
- Sync worker는 source item/document-level KB 단위 fencing token, lock expiry, idempotency key, bounded concurrency를 사용한다.

### Source Identity, Content, and Egress

- Protected source identity는 user-facing field가 아니라 protected table/ref에 저장한다. Raw source id, URL, principal, path는 keyed HMAC-SHA256 safe ref, key version, rotation/backfill, tombstone matching 정책으로 다룬다.
- `document_chunks.content`, embedding input, retrieval-visible artifacts, prompt context는 redacted canonical text를 기본 원천으로 사용한다.
- Raw source content는 기본 저장하지 않는다. Opt-in raw artifact storage는 encryption, raw/compliance permission, raw access audit, legal hold, retention, purge SLA, source policy approval이 모두 있어야 한다.
- Knowledge/RAG server-side outbound fetch/probe/sync/preview는 shared outbound client/factory와 OutboundEgressGuard를 통과한다. Guard는 host/IP/port/proxy/timeout/size/scheme/TLS/redirect를 검증하고, DB/SSH/SaaS/object-storage adapter는 protocol-specific safe behavior를 담당한다.

### Source Item Granularity

- 일반 source item/document는 document-level KB 하나로 매핑한다.
- Slack 계열의 임시 기본값은 다음과 같다.
  - Slack channel은 Knowledge Collection이다.
  - Slack thread, huddle recap, canvas, bot-generated meeting summary, pinned-message group은 document-level KB다.
  - Channel digest는 opt-in connector policy가 있을 때만 만든다.
  - DM, raw audio, raw transcript ingestion은 기본 제외한다.
- Slack/meeting artifact의 effective ACL baseline은 artifact-level ACL이 있으면 artifact ACL과 containing channel/workspace ACL의 교집합이다. Artifact ACL이 없거나 확인할 수 없으면 fail-closed 또는 remediation 상태로 둔다. Channel membership만으로 huddle recap, canvas, meeting summary를 자동 공개하지 않는다.

### Retrieval, Builder, and Runtime

- Workflow에는 별도 RAG node를 만들지 않는다. Knowledge retrieval은 LLM node의 RAG option/runtime path로 연결한다.
- Builder, router, runtime은 Knowledge Permission Helper가 만든 authorized safe candidate set만 소비한다. Raw permission row, raw ACL fact, hidden KB id/name, exact denied count, raw source title/path/url은 전달하지 않는다.
- Explicit KB mode는 collection route 권한을 생략할 수 있지만 KB `use`, source ACL, metadata filter, hierarchy mode, final evidence policy를 생략할 수 없다.
- Auto collection mode는 route-allowed collections와 permission helper-authorized KB에서 safe candidate set을 만든다.
- Runtime RAG는 명시 `execution_subject` 기준으로 평가한다. Workflow owner/builder permission fallback은 금지한다.
- Deployment preflight는 LLM node RAG option이 intended execution subject/audience에게 사용 가능한지 검사하고, safe status/reason/action만 반환한다.

### Query Rewrite, Evidence Sufficiency, and Source Tier

- `query_rewrite_mode` 기본값은 `off`다. Deterministic/template rewrite는 opt-in 후보로 허용할 수 있다.
- `llm_assisted` rewrite는 LLMOps/cost/security/credential/timeout/fallback gate가 별도로 닫히기 전까지 구현하지 않는다.
- Raw rewritten query는 audit, trace, usage, durable summary에 저장하지 않는다. Applied flag, strategy, safe template id, safe hash/summary만 허용한다.
- `evidence_sufficiency_policy` 기본값은 `minimum_evidence`다. 근거가 없거나 score/coverage/citation 기준을 만족하지 못하면 safe no-result 또는 insufficient-evidence response로 닫는다.
- Legal, policy, compliance, high-risk flow는 `strict_citation`을 기본 후보로 삼고, authoritative source tier 또는 두 개 이상의 독립 safe citation 같은 더 엄격한 기준을 사용한다.
- Source-of-Truth Tier는 authorized evidence 안에서 ranking, tie-break, conflict resolution hint로만 사용한다. Tier는 permission이나 source ACL을 대체하지 않는다.

### Resource Hiding, Summary, and Observability

- Scope 밖, organization mismatch, hidden deleted/archived resource, 존재 추론이 가능한 requester source authorization denied 또는 source ACL stale/unmapped/ambiguous/unverified/revoked 상태는 resource-hidden/404 또는 safe no-result로 닫고 hidden ids/counts를 만들지 않는다.
- Hidden/resource-hidden path의 external reason code는 `resource.hidden`으로 일반화한다. `source_acl.*` 세부 reason은 이미 존재가 authorized context에서 보이는 resource, admin/remediation context, 또는 내부 safe audit/trace allowlist에서만 사용한다.
- Partial result는 permission/source ACL/final evidence gates 이후 발생한 operational failure에만 허용한다. 응답과 trace는 `partial_result=true`, bucketed failed count, safe reason summary, retryability만 포함한다.
- Audit allowlist와 trace allowlist는 success/authorized path와 hidden/denied path를 분리한다.
- Raw query, raw rewritten query, raw chunk, raw source URL/path/title/principal, hidden KB id/name/count, raw prompt/completion/provider response는 audit, trace, log, usage summary, citation summary에 저장하지 않는다.
- Metrics labels는 bounded enum과 bucket만 사용한다.

### Operational Defaults

초기 구현값은 운영 설정으로 조정 가능해야 하며 제품의 고정 계약으로 쓰지 않는다. 상세 수치와 rollout 전 load-test 기준은 [Knowledge implementation baseline](../features/knowledge/implementation_baseline.md)의 운영 기본값 표가 소유한다. 이 ADR은 해당 표를 MBA-105의 임시 기준으로 채택한다.

### Test Phase Baseline

- Phase 1: egress negative paths, protected source identity, basic sync, redaction, active version swap, transactional outbox insert, fencing token, recovery scanner smoke.
- Phase 2: source ACL freshness, content cursor와 ACL/permission watermark 분리, permission helper, KB `use` + source ACL two-gate, source-policy grant inactive lifecycle.
- Phase 3: multi-KB caps, final evidence recheck, resource hiding matrix, retry/dead-letter transition, partial result behavior.
- Later: golden questions, source tier tuning, LLM-assisted rewrite, advanced rerank.

## Consequences

- [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)의 execution gates와 [ADR-0015](ADR-0015-knowledge-skill-context-routing-boundary.md)의 Knowledge Skill runtime safety gates 중 이 ADR이 값을 정한 항목은 MBA-105 구현의 임시 baseline으로 닫힌 것으로 본다. Skill authoring UI, publish/deprecate UX, code-bearing skill, Workflow Playground 연결은 여전히 범위 밖이다.
- Cross-domain owner가 다른 결정을 늦게 제공하더라도 Knowledge 구현은 adapter/helper/service boundary로 결합도를 낮춰야 한다.
- 후속 결정과 충돌하면 해당 adapter/helper, policy configuration, API matrix, migration boundary만 수정할 수 있어야 한다.
- 이 ADR은 destructive production migration, raw artifact storage opt-in, code-bearing Knowledge Skill, global/main Agent retrieval path, Workflow runtime 전체 egress guard를 승인하지 않는다.

## Non-Goals

- 이 ADR은 현재 코드가 목표 baseline을 이미 충족한다고 선언하지 않는다.
- 이 ADR은 raw content를 RAG/embedding/prompt/retrieval-visible artifact로 사용하는 것을 승인하지 않는다.
- 이 ADR은 source ACL만으로 KB content retrieval을 승인하지 않는다.
- 이 ADR은 LLM-assisted rewrite, code-bearing skill, break-glass source ACL bypass, workflow owner fallback을 승인하지 않는다.
- 이 ADR은 platform-wide Workflow outbound guard 정책을 승인하지 않는다.
