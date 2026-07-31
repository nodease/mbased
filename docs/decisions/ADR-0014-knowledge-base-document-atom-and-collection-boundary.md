# ADR-0014: Knowledge Base 문서 단위 원자와 Collection 경계

Status: Accepted

Related ADRs: [ADR-0006](ADR-0006-accept-rbac-auth-state-and-user-direct-permission.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0012](ADR-0012-metadata-aware-hierarchical-rag-boundary.md), [ADR-0013](ADR-0013-rag-answer-trace-usage-correlation-boundary.md)

## Context

현재 Knowledge/RAG 구현은 `knowledge_bases`를 여러 `documents`를 담을 수 있는 RAG data source 상위 단위로 사용한다. 이 모델은 수동 업로드와 단일 KB retrieval에는 동작하지만, mbased가 Drive, Wiki, ticketing tool, chat archive, future source connector 같은 외부/내부 시스템에서 사내 지식을 자동 수집해야 하면 의미가 모호해진다.

자동 수집에는 다음 제약이 있다.

1. 원본 source system은 개별 source item 단위로 접근 제어를 제공할 수 있다.
2. RAG retrieval은 collection 전체를 숨기거나 변경하지 않고도 특정 문서/source item 하나만 fail-closed로 제외할 수 있어야 한다.
3. 사용자가 탐색하고 라우팅하며 운영하는 묶음 단위는 content retrieval 권한 원자와 다르다.

초기 설계에서는 document-level ACL table을 직접 추가하는 방향도 검토했다. 하지만 이 방식은 기존 KB permission helper를 중복하고 RBAC를 복잡하게 만든다. 더 안전한 목표 구조는 기존 KB permission 개념을 문서/source item 단위 permission atom으로 재정의하고, 별도의 collection 계층을 grouping/routing/UX/ops 단위로 두는 것이다.

## Decision

`KnowledgeBase`는 문서/source item 1개에 대응하는 permission, retrieval, sync, lifecycle 원자로 재정의한다.

`KnowledgeCollection`은 grouping, routing, UX, operations 단위다. 하나의 collection은 collection item을 통해 여러 document-level KB를 묶을 수 있다. Collection permission은 그 자체로 하위 KB content retrieval 권한을 부여하지 않는다.

목표 모델은 다음과 같다.

```text
knowledge_collections
  -> knowledge_collection_items
      -> knowledge_bases
          -> document_versions
              -> document_chunks
```

Retrieval permission 모델은 two-gate 구조다.

1. 요청자는 document-level KB에 대한 mbased KB `use` 권한을 가져야 한다.
2. Source-managed KB라면 요청자는 fresh source ACL/requester authorization gate도 통과해야 한다.

Source ACL state는 별도의 병렬 권한 시스템이 아니며, router나 LLM planner가 직접 해석하지 않는다. Source ACL fact는 source ACL provenance로 매핑되고 permission helper가 소비한다. Source ACL state가 stale, unmapped, ambiguous, unverified, revoked이면 source-managed KB retrieval은 fail-closed로 제외한다. Manual KB grant는 mbased KB permission gate만 만족시킬 수 있고, 향후 break-glass 예외 ADR이 명시적으로 추가되지 않는 한 source ACL freshness/requester authorization을 우회하지 못한다. Organization owner/manager override는 mbased 관리 또는 remediation 권한을 만족시킬 수 있지만, 기본적으로 retrieval requester의 source ACL authorization을 우회하지 못한다.

Source ACL은 KB `use` 권한을 자동으로 대체하지 않는다. Source-managed KB에서 대량 자동 수집 문서가 실제 retrieval 후보가 되려면 구현 전에 다음 중 하나를 gate로 닫아야 한다.

- 관리자가 team/user KB grant를 별도로 부여하고 source ACL은 두 번째 gate로만 사용한다.
- Source ACL sync가 source-owned KB `use` grant를 materialize하되, 저장 table, freshness gate, revocation, audit-safe provenance, manual grant와의 우선순위를 별도 ADR/RBAC 문서로 확정한다.
- 다른 helper-level 결합 모델을 사용하되, router/retrieval이 source ACL row나 permission row를 직접 조합하지 못하게 한다.

어떤 방식을 선택하더라도 source ACL authorization만으로 KB `use`가 충족됐다고 해석하지 않는다. `source_knowledge_permission_grants`는 source ACL provenance를 permission helper가 사용할 수 있게 하는 후보 저장소이며, 최종 schema와 의미는 source ACL materialization gate에서 확정한다.

Raw source content는 기본 durable retrieval artifact가 아니다. 목표 구조에서 chunk text, embedding input, retrieval-visible text는 기본적으로 redacted canonical text에서 생성한다. Raw content는 protected raw knowledge artifact로만 저장할 수 있다. Raw embedding input, raw retrieval-visible artifact, prompt/Agent answer stream에 raw content를 넣는 행위는 금지한다.

Privacy/redaction은 audit-only 관심사가 아니라 Knowledge 목표 경계의 일부다. 목표 아키텍처는 Audit/Tracing과 Knowledge가 함께 사용하는 shared privacy/redaction service 경계를 둔다.

```text
shared/services/privacy/redaction
  -> Audit/Tracing payload redaction, trace metadata sanitizer, raw/redacted payload policy
  -> Knowledge source normalization, redacted canonical text, citation preview redaction
```

Shared privacy/redaction service는 공통 detector와 masking engine을 소유한다.

- PII/secret detector
- masking/hash/drop/block rule
- output target별 redaction behavior
- 관리자가 약화할 수 없는 platform hard baseline

Audit/Tracing은 trace payload storage, trace visibility, raw/redacted trace retention, trace payload access audit을 계속 소유한다. Knowledge는 source normalization, redacted canonical text 생성, chunking, embedding input, citation preview, raw artifact reference를 소유한다.

PII 정책은 두 계층으로 구성한다.

1. 관리자가 약화할 수 없는 platform hard baseline.
2. Organization, collection, source, KB 단위로 더 엄격하게 조정할 수 있는 policy override.

Organization/source policy가 명시적으로 opt-in한 경우에만 raw source content를 protected raw knowledge artifact로 저장할 수 있다. Raw content는 `document_chunks.content`에 저장하지 않고, embedding input으로 사용하지 않으며, retrieval-visible text로 쓰지 않고, prompt나 Agent answer stream에 넣지 않는다. `document_chunks.content`, embedding input, retrieval-visible text, default citation preview는 redacted canonical text에서 생성한다. Citation preview는 user-facing response 전용이고 redaction과 길이 cap을 거치며 durable audit, trace, usage summary에 복사하지 않는다.

Raw content 저장을 활성화하면 별도의 protected store를 사용한다.

```text
document_versions / document_chunks
  = redacted canonical text and retrieval artifacts

raw_knowledge_artifacts or encrypted object storage + metadata table
  = compliance viewing 전용 protected raw source content
```

Raw artifact metadata는 organization scope, KB/version/source identity reference, storage reference, encryption key version, content hash, retention expiry, legal hold state, purge state, audit-safe timestamp를 포함해야 한다. Raw value나 object key는 audit, trace, log, router input, citation summary에 노출하지 않는다.

Raw content access는 RAG answer generation과 분리된 별도 flow다.

1. Active organization과 KB visibility를 검증한다.
2. Raw/compliance permission을 검증한다.
3. Source-managed KB라면 fresh source ACL/requester authorization을 검증한다.
4. Retention, legal hold, purge policy를 검증한다.
5. Content 반환 전에 raw access audit을 먼저 기록한다.
6. Dedicated raw/compliance endpoint 또는 surface에서만 raw content를 반환한다.

Knowledge/RAG source collection에 사용하는 모든 server-side outbound network access는 protocol adapter가 fetch, probe, sync, preview를 수행하기 전에 중앙 outbound egress boundary를 통과해야 한다. 대상에는 source connector test/preview/fetch/sync, `/api/v1/rag/proxy/preview`, `s3FileUrl`/`apiUrl` 같은 URL upload/preview field, crawler/sitemap/API connector, DB/SSH/SaaS/object-storage probe가 포함된다. Egress boundary는 host/IP/port/proxy/timeout/size policy를 처리하고, protocol adapter는 SQL/command 제한 같은 protocol-specific safe behavior를 처리한다. 이 ADR은 Workflow runtime HTTP/GitHub/Mail node 전체에 대한 platform-wide outbound policy를 승인하지 않는다. 그 범위는 별도 runtime egress policy/ADR이 필요하다.

## Execution Gates

이 ADR은 목표 도메인 의미를 승인한다. 하지만 destructive migration, endpoint rename, 모든 목표 table 구현을 이 ADR만으로 승인하지 않는다.

아래 gate는 해당 구현 작업 전에 닫아야 한다.

| Gate | 구현 전 필요한 결정 |
| --- | --- |
| Data preservation and cutover | 환경 reset/reindex 가능 여부, backup/export 계획, rollback 한계, 기존 multi-document KB split/backfill 계획, team/user permission 보존, RAG answer reference 처리, destructive migration 명시 승인. 이 gate가 닫히기 전 schema 변경은 additive로 제한한다. |
| ID vocabulary | API, audit, trace, citation, UI field에서 `knowledge_base_id`, `collection_id`, `document_version_id`, source identity reference, legacy `document_id`를 어떻게 사용할지 확정한다. |
| Active version finalization | Transaction 순서, outbox behavior, owner-token lock release, fencing token, recovery scanner, finalization 전 artifact visibility, `content_hash`/fingerprint commit boundary, ACL-only invalidation, crash recovery, object storage/vector index cleanup 순서를 확정한다. |
| Source ACL materialization | Source principal mapping, ACL provenance table, source-derived authorization 의미, content cursor와 ACL/permission watermark 분리, ACL-only idempotency, freshness epoch, revocation fast path, cache invalidation, requester authorization result shape, source-owned KB `use` grant를 만들지 여부와 저장 방식을 확정한다. |
| Resource hiding API matrix | Explicit KB mode와 auto collection mode의 not found, no use permission, source ACL denied/stale/unmapped/ambiguous, archived, deleted, permission-unverified, no-authorized-candidate auto result, hidden aggregate 응답을 확정한다. |
| Collection permission storage | Collection permission을 resource-specific team/user table로 둘지 별도 subject table로 둘지, router가 row를 직접 읽지 못하게 하는 helper contract를 확정한다. |
| Protected source identity | HMAC key versioning, rotation/backfill, tombstone matching, display metadata policy, safe external reference format을 확정한다. Raw source id/url/principal 값은 user-facing identity가 아니다. |
| Canonical metadata and privacy source | 현재 `documents.meta_info`를 목표 `document_versions.metadata`, protected source identity field, shared privacy/redaction policy, optional raw artifact reference로 어떻게 대체할지 확정한다. |
| Raw artifact and privacy controls | Raw artifact storage schema, raw/compliance permission enum, raw access audit action/reason code, retention/legal hold/purge SLA, terminal-status-only purge, purge row locking/marker, shared privacy/redaction service 추출 경계를 확정한다. |
| Retry/dead-letter vocabulary | Sync run state, retry/backoff, cursor commit rule, dead-letter reason, operational remediation contract를 확정한다. |

## Consequences

- [docs/glossary.md](../glossary.md), [docs/data_model.md](../data_model.md), [docs/features/knowledge](../features/knowledge/requirements.md)는 migration이 구현되기 전까지 현재 구현 baseline과 목표 KB 통합 모델을 구분해야 한다.
- [ADR-0012](ADR-0012-metadata-aware-hierarchical-rag-boundary.md)는 현재 metadata-aware/hierarchical RAG 경계로 유지한다. 다만 목표 KB 통합은 code migration 전에 canonical metadata gate를 통해 `documents.meta_info` source-of-truth 가정을 대체해야 한다.
- [ADR-0013](ADR-0013-rag-answer-trace-usage-correlation-boundary.md)은 현재 standalone single-KB Agent answer lifecycle과 trace/usage correlation 경계로 유지한다. ADR-0014의 resource hiding matrix는 target KB cutover, source-managed KB, auto collection, multi-KB mode에 필요한 추가 gate이며, ADR-0013의 현재 단일 KB 계약을 다시 여는 결정이 아니다.
- Multi-KB 또는 collection-routed Agent answer flow는 계속 `rag_answer_runs`를 RAG-owned anchor로 사용하고, 별도 ADR 없이 trace/usage table에 RAG-specific FK를 추가하지 않는다.
- Router, LLM planner, UI collection list는 authorized safe candidate와 safe metadata만 받는다. 이 계층은 access control을 수행하지 않고 raw source ACL data를 보지 않는다.
- Metrics, audit, trace, API summary는 hidden document existence를 누출하지 않아야 한다. Count와 source distribution은 resource hiding matrix에 따라 omit, request-scoped only, bucket 처리 중 하나로 제한한다.
- Public source ACL은 기본적으로 organization-wide read/use가 되지 않는다. Opt-in materialization에는 connector policy, organization policy, approver, expiry/reverification, revocation behavior, audit-safe metadata가 필요하다.
- Admin configurable PII rule은 처리를 강화하거나 승인된 display mode를 선택할 수 있지만 hard baseline을 비활성화할 수 없다.
- Protected raw artifact storage를 활성화하기 전 raw content retention, legal hold, purge, access audit은 구현 blocker다.
- Target ingestion은 owner-token/fencing 기반 lock과 idempotent outbox를 갖기 전까지 processed state를 먼저 commit하거나 physical artifact를 DB commit보다 먼저 삭제하는 흐름을 target-safe한 것으로 보지 않는다.

## Non-Goals

- 이 ADR은 collection router나 multi-KB retrieval을 구현하지 않는다.
- 이 ADR은 raw content를 RAG, embedding, prompt, retrieval-visible artifact로 사용하는 것을 승인하지 않는다.
- 이 ADR은 KB permission/source ACL provenance 모델 밖의 document-level ACL table을 추가하지 않는다.
- 이 ADR은 public source ACL을 organization-wide read/use로 자동 승인하지 않는다.
- 이 ADR은 raw/compliance access permission 이름이나 inheritance behavior를 승인하지 않는다.
- 이 ADR은 모든 organization/source가 raw content를 저장해야 한다고 요구하지 않는다.
- 이 ADR은 trace visibility나 trace retention policy를 대체하지 않는다. 공통 redaction과 trace-specific storage 책임을 분리할 뿐이다.
