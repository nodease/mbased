# ADR-0065: Knowledge 분류, 조직 taxonomy와 processing profile 경계

Status: Accepted

## Related Decisions

- [ADR-0007](ADR-0007-mvp2-classification-metadata-storage.md)
- [ADR-0010](ADR-0010-resource-access-403-404-policy.md)
- [ADR-0012](ADR-0012-metadata-aware-hierarchical-rag-boundary.md)
- [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)
- [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md)
- [ADR-0034](ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md)
- [ADR-0036](ADR-0036-knowledge-runtime-candidate-resolution.md)
- [ADR-0052](ADR-0052-knowledge-document-ingestion-durable-execution-boundary.md)
- [ADR-0057](ADR-0057-llm-credential-at-rest-encryption-and-rotation.md)
- [ADR-0064](ADR-0064-provider-execution-capability-boundary.md)

## Context

현재 `documents.meta_info.classification`은 ADR-0007이 정의한 문서 보안 민감도
metadata convention이다. 값은 `public`, `internal`, `confidential`, `pii`이며 권한의
source가 아니다. 한편 Knowledge 추천에 사용하는 safe topic, 문서 구조에 따른 parser 및
chunk 전략, 조직의 업무 분류와 AI가 제안하는 label은 서로 다른 owner, lifecycle과 실패
영향을 가진다.

이 의미들을 하나의 `classification` 또는 자유 형식 tag에 합치면 다음 위험이 생긴다.

- AI topic 오류가 보안 등급, permission 또는 retention 판단으로 확대될 수 있다.
- 조직 taxonomy의 rename, move, deprecate가 기존 document/version lineage를 끊는다.
- 수동 지정, deterministic rule과 AI suggestion의 우선순위와 잠금 상태를 재현할 수 없다.
- 문서 유형별 processing profile 선택이 mutable label이나 미보정 confidence에 의존한다.
- profile 변경 중 indexing이 실패하면 현재 검색 가능한 active version을 잃을 수 있다.

MBA-279의 development benchmark는 모든 문서 유형과 질의에서 하나의 chunk profile이
일관되게 우세하지 않음을 보였다. 이 결과는 profile을 versioned policy로 비교해야 한다는
탐색 근거지만 특정 chunk 크기, 계층형 검색 또는 AI classifier를 production 기본값으로
승인하는 근거는 아니다.

## Evidence Considered

다음 연구는 특정 Nodease 기본값이 아니라 교체 가능하고 평가 가능한 구조를 선택하는 근거로
사용했다.

- [Rethinking Chunk Size For Long-Document Retrieval](https://arxiv.org/abs/2505.21700)은
  dataset과 embedding model에 따라 유리한 chunk size가 달라질 수 있음을 보고한다. 따라서
  하나의 전역 숫자보다 token-aware versioned profile과 document/query strata 평가를 사용한다.
- [RAPTOR](https://openreview.net/forum?id=GN921JHCRw),
  [LongRAG](https://arxiv.org/abs/2406.15319),
  [Late Chunking](https://arxiv.org/abs/2409.04701)은 각각 summary hierarchy, long retrieval
  unit과 contextual embedding의 가능성을 보여준다. 서로 다른 비용과 failure mode가 있으므로
  하나의 advanced RAG 기본값으로 합치지 않는다.
- ACL/EMNLP의 [hierarchy-aware classification](https://aclanthology.org/2020.acl-main.104/),
  [hierarchical multi-label classification](https://aclanthology.org/2021.emnlp-main.190/)과
  [few/zero-shot label 연구](https://aclanthology.org/2020.emnlp-main.607/)는 hierarchy,
  multi-label, sparse label과 taxonomy update를 별도 문제로 다룬다. V1 single-parent forest와
  stable version lifecycle 자체는 운영 복잡도를 제한하기 위한 제품 결정이다.
- [On Calibration of Modern Neural Networks](https://proceedings.mlr.press/v70/guo17a.html)과
  [Selective Classification](https://arxiv.org/abs/1705.08500)은 raw confidence를 correctness
  probability로 간주하지 않고 calibration과 abstention/risk-coverage를 분리해야 함을 지지한다.
- [HYRR](https://aclanthology.org/2024.lrec-main.748/)은 candidate generation과 reranking을
  분리해 평가할 근거를 제공한다. 특정 reranker가 Nodease corpus에서도 우세하다는 뜻은 아니다.

MBA-279 development 결과에서도 동일 2,048-token budget의 cluster-macro 차이는 confidence
interval이 0을 포함했고, 개인정보·절차 cluster와 전체 평균의 profile winner가 달랐다.
Hierarchical Top-5 recall은 Flat보다 1.11%p 높았지만 대부분 tie였으며, required-evidence miss의
다수는 diagnostic Top-100 안에 남아 chunk boundary뿐 아니라 ranking/diversification 병목이
관찰됐다. 이 결과는 factor 분리와 confirmatory benchmark 필요성을 지지할 뿐 production
winner를 정하지 않는다.

## Options Considered

1. 기존 `classification` 값을 문서 유형, topic과 processing profile까지 확장한다.
   - 저장은 단순하지만 보안 민감도와 검색 품질 metadata의 권위가 섞인다.
2. Organization이 모든 분류 값을 자유 문자열 또는 하나의 generic tag graph로 관리한다.
   - 업무 용어에는 유연하지만 parser와 index runtime이 이해하는 capability identity,
     version과 migration 계약이 없다.
3. 보안 분류, platform document type, organization taxonomy와 processing profile을 분리하고
   assignment, suggestion과 activation을 versioned contract로 관리한다.
   - 초기 모델과 API가 늘어나지만 권위, provenance, 보안과 rollback 경계를 독립적으로
     검증할 수 있다.
4. Label 비교, topic cardinality와 replacement traversal을 구현별 설정으로 남기거나 V1 고정 계약으로 둔다.
   - 구현별 설정은 초기 유연성은 있지만 Client/server/test의 경계값이 갈리고 historical replacement graph가
     무제한으로 깊어질 수 있다. V1은 versioned server normalizer, assignment topic 32개와 replacement path
     8 edge를 고정하고 변경 시 새 계약 version과 ADR 검토를 요구한다.
5. Processing profile을 platform catalog로만 제한하거나 Organization이 자체 profile을 관리하게 한다.
   - Platform-only는 초기 구현은 작지만 Organization mapping policy가 자기 업무 문서에 맞춘 exact revision을
     만들 수 없다. V1은 platform read-only catalog와 별도로 Organization manager가 관리하는 immutable-revision
     catalog lifecycle을 제공한다.
6. Canonical content가 바뀐 assignment를 그대로 사용하거나, 모든 처리를 차단하거나, axis authority에 따라
   carry-forward와 fallback을 분리한다.
   - 무조건 승계는 unrelated content에 stale profile을 적용하고, 전면 차단은 기존 active-ready 가용성을
     해친다. V1은 locked axis만 review-recommended 상태로 계속 사용하고 unlocked stale axis는 resolver에서
     제외해 general fallback으로 처리한다.
7. Manifest/Satisfaction reference를 physical retention pin으로 간주하거나 provenance와 availability를 분리한다.
   - 모든 provenance reference가 bytes를 영구 pin하면 storage가 무한 증가한다. V1은 immutable manifest/link를
     보존하되 active/citation/retention/legal-hold pin만 physical deletion을 막고 purge tombstone으로
     availability를 별도 기록한다.
8. Taxonomy impact preview를 UI 도움말로 두거나 publish precondition으로 결속한다.
   - 선택 사항이면 직접 API 호출이 조직 전체 assignment 영향을 확인하지 않고 publish할 수 있다. V1은
     same-snapshot impact preview token과 explicit acknowledgement를 모든 taxonomy publish에 요구한다.

## Decision

### 1. 네 축을 독립된 source of truth로 둔다

| 축 | 의미 | 권위와 scope |
| --- | --- | --- |
| `security_classification` | 문서 보안 민감도 | ADR-0007의 `public/internal/confidential/pii`. 현재는 `documents.meta_info.classification`이 compatibility source다 |
| `document_type` | parser/profile이 이해하는 구조 capability | Platform-managed, versioned registry의 stable opaque ID. Effective assignment revision당 하나다 |
| `taxonomy_topic_ids` | 조직의 업무 주제 | Organization-scoped, versioned taxonomy의 stable opaque ID 집합과 optional primary topic이다 |
| `chunking_profile_ref` | exact processing configuration | Immutable profile revision reference다. Assignment 자체가 아니라 deterministic resolver 결과다 |

`security_classification`은 topic, document type 또는 profile로부터 자동 승격·강등하지 않는다.
Topic, type, safe recommendation metadata와 profile은 permission, source ACL, retention 또는 legal
status의 source가 아니다.

### 2. Document type과 organization taxonomy의 owner를 분리한다

`document_type`은 platform이 관리하는 versioned capability registry다. Runtime이 지원하는
parser, structure preservation과 profile mapping capability를 stable ID로 표현한다. 최초 실제
type 목록과 organization extension 방식은 registry 구현 이슈에서 확정하며, 임의 문자열을
runtime capability로 해석하지 않는다.

V1 semantic assignment granularity는 document-level이다. 본문, 표, 부록 등 구조가 섞인 문서는
registry의 `mixed` capability type으로 표현하고 parser가 block별 `structure_kind`와 canonical
locator를 보존한다. Profile은 block fact에 따라 boundary strategy를 달리할 수 있지만 V1에서
section/chunk-level semantic type/topic assignment를 만들지는 않는다. 향후 section assignment는
document assignment를 덮어쓰지 않는 additive 모델로 별도 승인한다.

Organization taxonomy는 stable opaque topic ID를 사용하는 immutable-version single-parent
rooted forest다. 한 문서는 여러 topic에 assignment될 수 있고 그중 하나만 primary가 될 수
있다. V1은 multi-parent DAG를 지원하지 않으며 여러 branch 의미는 document multi-label로
표현한다.

V1 taxonomy version은 complete-tree authoring contract를 사용한다. Version 하나는 topic definition을
최대 1,000개, candidate `replacement_topic_ids` edge를 합계 최대 2,000개까지 포함할 수 있다. Topic
raw label은 UTF-8 512 byte 이하여야 하고 Create/PATCH는 duplicate 제거 또는 normalization 전에 raw
topic/edge 수와 raw label byte bound를 검사한다. Server normalization 뒤 comparison key도 UTF-8 512 byte
이하여야 하며 이 post-normalization bound는 sibling uniqueness, graph traversal과 DB/provider/mutation 전에
검사한다. 초과 payload는 각각 `knowledge.taxonomy_topic_count_limit_exceeded`,
`knowledge.taxonomy_replacement_edge_limit_exceeded` 또는 `knowledge.taxonomy_label_invalid`로 mutation 없이
거부한다. 따라서 version detail은 topic pagination 없이 complete forest를 반환하되 항상 위 bound 안에 있다.
한도를 늘리거나 partial topic pagination/editing을 도입하려면 새 versioned API contract와 recovery/CAS 정책을
별도로 승인한다.

Stable `topic_id`는 server만 발급한다. Client는 새 topic에 UUID 형식의 `draft_topic_key`를 붙이고, 같은
request의 parent/replacement는 existing server `topic_id` 또는 새 topic의 `draft_topic_key`를 discriminated
reference로 사용한다. Server는 성공한 draft transaction에서 key를 stable `topic_id`에 매핑하고 mutable draft
detail의 current complete-tree snapshot에 mapping을 보존·반환한다. Mapping 수는 current topic 수 이하라서
최대 1,000개다. 같은 allocation request 안의 parent/replacement reference만 새 key를 반복해 같은 ID로
resolve할 수 있다. 성공한 allocation은 current snapshot에서 key를 consume하므로 이후 mutation은 server
`topic_id`를 사용해야 하고, current-mapped key를 topic definition 또는 relation reference로 다시 제출하면
`409 knowledge.taxonomy_draft_topic_key_conflict`다. Complete PATCH가 topic을 제거하면 그 unpublished recovery
mapping도 같은 CAS transaction에서 제거한다. 이후 같은 client key를 다시 쓰면 server는 새 stable ID를
발급하며 제거된 ID를 재사용하지 않는다. Topic definition의 key는 allocation request 안에서 unique하다.
Missing/invalid/non-unique/out-of-draft key, key와 existing ID 동시 지정 또는 잘못된 reference shape는
`422 knowledge.taxonomy_draft_topic_reference_invalid`다. 모든 실패는 ID/mapping/revision partial write
없이 닫는다. 응답 유실 뒤 stale retry는 성공으로 가장하지 않고
draft detail을 다시 조회해 mapping과 current `draft_revision`을 복구한다. `draft_topic_key`는
authoring recovery용 비권위 reference이며 assignment, published API와 runtime identity로 사용할 수 없다.
Published/superseded projection은 stable `topic_id`만 반환하고 draft key를 노출하지 않는다.

Topic label은 identity가 아니지만 authoring ambiguity를 막기 위해 같은 parent의 direct child
사이에서 canonical comparison key가 유일해야 한다. Root topic은 모두 하나의 implicit root를 parent로
가진 sibling으로 평가한다. Server는 Unicode Character Database 14.0.0을 고정한 versioned
`unicode_14_0_nfkc_casefold_ws_v1` contract로 label을
Unicode NFKC 정규화하고 full Unicode case-fold한 뒤 다시 NFKC 정규화하며, Unicode White_Space를
U+0020 한 칸으로 축약하고 양끝을 제거해 comparison key를 만든다. Server는 이 단계 직후 comparison key의
non-empty와 UTF-8 512 byte bound를 검사한 다음 sibling uniqueness와 graph validation을 수행한다. 빈 key와 같은
sibling key는 각각 `knowledge.taxonomy_label_invalid`와 `knowledge.taxonomy_sibling_label_conflict`다. 다른
parent 아래의 같은 key는 허용한다. Client가 보낸 comparison key는 authority가 아니며 taxonomy version은 normalizer contract
version을 snapshot한다. Runtime Unicode library가 업그레이드되어도 Unicode Character Database 14.0.0
결과를 보존하고, Unicode data version 변경은 새 normalizer contract와 새 taxonomy version으로만
도입한다.

Deprecated topic은 같은 Organization의 candidate version에서 active인 topic을 가리키는 optional
`replacement_topic_ids`를 가질 수 있다. 여러 old topic이 하나를 가리키는 merge와 하나가 여러 target을
가리키는 split hint는 허용하지만 assignment를 자동 이관하지 않는다. Organization-lifetime published
replacement edge와 candidate edge를 합친 graph는 self-reference, missing/cross-organization
target과 cycle을 허용하지 않고 모든 directed path를 최대 8 edge로 제한한다. Candidate edge로 경로가
9 edge 이상이 되면 `knowledge.taxonomy_replacement_depth_exceeded`, 그 밖의 잘못된 reference/cycle은
`knowledge.taxonomy_replacement_invalid`다. Validate는 이 code와 label violation을 fixed safe result로
반환한다. Unresolved violation이 있는 publish는 `409 knowledge.taxonomy_publish_validation_failed`로
종료하고 published version, current pointer, identity/replacement ledger와 canonical audit를 만들지 않는다.

Topic이 0개인 taxonomy version도 유효한 empty forest다. 신규 organization이 taxonomy 없이
document type/general profile만 사용하거나 기존 taxonomy를 명시적으로 비활성화할 수 있도록 fake root를
강제하지 않는다. 다만 empty version publish도 일반 publish와 동일한 impact preview, 교차 snapshot
compatibility 검증과 canonical audit를 거쳐야 한다. Current profile policy에 topic rule이 남아 있으면
해당 policy를 먼저 호환 가능한 version으로 publish하기 전에는 empty taxonomy를 publish할 수 없다.

Impact preview는 선택적 read가 아니라 publish admission precondition이다. Preview는 Organization/actor,
candidate version과 exact draft revision, nullable current taxonomy/profile-policy, document-type registry,
profile catalog 및 server-owned assignment impact snapshot revision을 bind한 최대 10분 수명의 opaque
`impact_preview_revision`을 반환한다. Token은 capability가 아니고 bounded impact bucket만 동반하며 hidden
KB/document identity나 exact denied count를 포함하지 않는다. Publish request는
`expected_impact_preview_revision`과 `impact_acknowledged=true`를 요구한다. Publish transaction은 current
permission과 위 snapshot을 다시 검증하고, token scope/expiry 또는 impact basis가 달라지면
`409 knowledge.taxonomy_impact_preview_stale`로 version/current pointer/ledger/audit 전체를 변경하지 않는다.
영향이 0개이거나 candidate가 empty forest가 아니어도 preview와 acknowledgement를 생략하지 않는다.
Assignment current pointer 또는 canonical content commit처럼 taxonomy impact 대상이 달라지는 mutation은
같은 transaction에서 Organization-scoped assignment impact snapshot revision을 전진시킨다. 구현은 동등한
serializable impact digest를 사용할 수 있지만 async projection lag를 publish 성공 근거로 사용하지 않는다.

Impact bucket은 `taxonomy_impact_bucket_v1` contract로 고정한다. 같은 authoritative snapshot에서 아래 exact
내부 count를 각각 계산한 뒤 `none=0`, `small=1..10`, `medium=11..100`, `large=101+`로 독립 변환한다.

- `affected_assignment_count`는 같은 Organization의 processing-eligible active KB/document에 연결된 current
  assignment 중 candidate taxonomy projection이 current published taxonomy projection과 다른 row를 하나씩 센다.
  비교 tuple은 topic-axis freshness와 resolver-eligible topic ID set/primary며
  exact taxonomy version ID 차이만으로 count하지 않는다. Null-taxonomy empty assignment의 first publish와
  prior-version empty assignment의 current-validation처럼 tuple이 바뀌는 경우는 포함한다. 이미 stale인 assignment가
  candidate에서도 같은 freshness/eligible set/primary를 유지하면 제외하고 assignment가 없는 KB는
  세지 않는다.
- `reindex_candidate_count`는 위 affected 집합 중 current active-ready artifact가 있는 assignment만 평가한다.
  Current Processing Decision과 비교 가능한 `materialization_input_fingerprint`가 있으면 사람이 자동 replacement를
  확인했다고 가정하지 않은 candidate projection의 fingerprint가 다른 assignment만 센다. Active-ready legacy
  artifact는 있지만 current decision 또는 비교 가능한 fingerprint가 없으면 동일 materialization을 증명할 수
  없으므로 보수적으로 reindex candidate에 포함한다. Resolved scoped profile ref revision/source/status만 바뀌고
  normalized materialization fingerprint가 같음이 증명되면 물리 재색인 후보에서 제외하고 새 Decision Manifest와
  `satisfied_existing`으로 수렴한다. 항상 affected count 이하이며 publish가 job을 만들거나 assignment를 바꾼다는
  뜻이 아니다.

Archived/deleted KB/document 또는 processing-ineligible target은 두 count에서 제외한다. Artifact availability
`purging|purged`는 assignment projection 차이를 없애지 않으므로 affected count에는 포함할 수 있지만
active-ready artifact가 아니므로 reindex candidate에서는 제외한다. 이 lifecycle/availability 전이가
`assignment_impact_snapshot_revision`을 전진시키는 이유다. Response와 publish precondition은
`impact_bucket_contract_version`을 포함하고 opaque preview revision도 이를 bind한다. Threshold나 count predicate가
바뀌면 새 contract version을 발급하며 old token은 stale이다. Exact count와 hidden identity는 server 내부
계산/검증에만 사용하고 response, audit 또는 token plaintext에 넣지 않는다.

Revision을 전진시키는 mutation은 normalized no-op을 제외한 assignment current pointer 생성·교체·삭제,
manual PUT/takeover, suggestion accept, current-validation, axis lock/unlock, 새 canonical materialization input hash,
impact 대상이 scope에 들어오거나 나가게 하는 KB/document lifecycle 전이, explicit profile override set/change/clear,
current Processing Decision pointer 변경과 active artifact pointer 또는 `ready|purging|purged` availability 전이를 포함한다.
이 processing 상태 변화는 `reindex_candidate_count`가 읽는 active-ready/current-decision predicate를
바꾸므로 pointer/availability mutation과 같은 Unit of Work에서 revision을 전진시킨다. Suggestion
create/reject/expire, read/preview, canonical input이 같은 source generation 변경, staging build/job/manifest 생성과
current pointer/availability를 바꾸지 않는 no-op은 전진시키지 않는다. Taxonomy/profile-policy/registry와
Organization/Platform profile catalog snapshot은 token에 별도 exact revision으로 bind된다. Revision increment와
authoritative mutation/audit는 같은 Unit of Work에서 commit 또는 rollback한다.

- Published taxonomy version은 수정하지 않는다. 변경은 새 draft version을 publish한다.
- Draft version은 별도 `draft_revision`을 가지며 PATCH는 observed revision을 CAS precondition으로
  요구한다. Validate, impact preview와 publish도 자신이 검사한 exact draft revision에 묶이고,
  publish는 current published pointer CAS를 함께 검증한다. Stale editor가 나중에 저장한 내용으로
  앞선 편집을 조용히 덮어쓰지 않는다.
- 최초 taxonomy 또는 profile-policy publish의 expected current pointer는 nullable exact precondition이다.
  `null`은 current pointer가 실제로 없을 때만 유효하며 publish transaction은 absence에서 candidate로의
  전환을 CAS한다. Current pointer가 이미 생겼는데 `null`을 보내거나, pointer가 없는데 non-null version을
  보내면 stale snapshot conflict다.
- Taxonomy validate/impact preview는 current profile-policy, document-type registry와 Organization/Platform
  profile catalog revision을 compatibility snapshot으로 반환한다. Publish는 이 snapshot과 current taxonomy pointer를
  모두 CAS하고 candidate taxonomy에 대해 current profile policy를 다시 검증한다. Profile-policy
  publish도 current taxonomy, document-type registry와 두 profile catalog revision을 같은 방식으로 검증한다.
  두 publish가 경합해 서로 호환되지 않는 current pair를 만들 수 없도록 pointer CAS 또는 동등한
  serializable boundary를 사용한다.
- Taxonomy와 profile-policy publish는 같은 Organization classification-policy coordination row 또는 동등한
  shared serialization primitive를 사용한다. Per-resource current pointer CAS만 각각 수행해서는 두 nullable
  pointer를 함께 관찰한 최초 publish의 write skew를 막을 수 없다. Catalog lifecycle과 current-reference
  mutation까지 포함한 전역 lock 순서는 applicable Organization catalog row, Platform catalog row,
  classification-policy coordination row, taxonomy current pointer, profile-policy current pointer 순서다.
  사용하지 않는 catalog row는 생략하되 뒤 단계 row를 잡은 뒤 앞 단계 catalog row를 역순 취득하지 않는다.
  Registry 및 Organization/Platform catalog snapshot도 commit 전에
  재검증한다.
- Rename과 move는 stable topic ID를 보존한다.
- Published version에 한 번 등장한 stable topic ID는 Organization 수명 동안 같은 logical topic identity로만
  유지한다. Organization-lifetime topic identity ledger는 first-published identity와 terminal tombstone을
  append-only로 보존하고 published replacement edge ledger는 from/to stable ID와 source taxonomy version을
  immutable하게 보존한다. Published successor에서 deprecated 또는 제거되어 tombstone이 된 ID는 다시 active
  topic으로 복원하거나 다른 의미에 재사용하지 않는다. 재도입하거나 분리한 의미에는 새 opaque ID를 발급한다.
  Ledger의 first-published/tombstone/replacement-edge append는 taxonomy current pointer와 canonical publish
  audit를 확정하는 같은 transaction에 포함하며 어느 write든 실패하면 전체 publish를 rollback한다.
- Deprecate는 assignment를 삭제하거나 replacement로 자동 변환하지 않는다.
- Merge와 split은 별도 migration/review workflow 없이 기존 assignment를 조용히 바꾸지 않는다.
- Deprecated 또는 현재 version에서 찾을 수 없는 topic은 provenance에는 남기되
  `review_required`다. Hard filter, ranking boost와 profile mapping 입력에서는 제외한다.

### 3. Effective assignment와 suggestion을 분리한다

목표 resource atom은 ADR-0014의 document-level Knowledge Base다. Effective assignment는 KB
scope에서 유지하되 `effective_from_content_revision_ref`와
`last_validated_content_revision_ref`를 구분하고 assignment revision을 기록한다. Current physical
`*_document_version_id`를 transition bridge로 유지하더라도 canonical content revision/hash에
매핑해야 하며 profile reindex가 만든 output DocumentVersion ID 자체로 freshness를 판단하지 않는다.
`document_type` 하나, topic set과 optional primary는 하나의 원자적 assignment revision으로
교체한다. Document type과 topic axis는 각각 authority source와 lock을 가지며 한 revision 안에서도
`document_type=manual`, `topics=rule`처럼 서로 다른 source를 가질 수 있다. Client가 보낸 부분 목록을
기존 set에 암묵적으로 merge하지 않는다.

Effective assignment와 classification suggestion candidate의 topic set은 최대 32개 stable topic ID다.
Client request 배열 길이는 중복 제거 전에 검사하고 duplicate ID를 조용히 제거하지 않는다. 33개 이상은
`knowledge.classification_topic_limit_exceeded`로 DB/provider/mutation 전에 거부한다. Rule/classifier가 만든
candidate는 policy/adapter 결과 직후 같은 bound로 검증하고 33개 이상이면 durable suggestion, assignment와
audit를 만들지 않는다. 이 bound는 classification metadata, audit/projection과 resolver fingerprint를
제한하기 위한 것이며 retrieval result 수나 chunk recall limit가 아니다.

KB-scoped current assignment pointer는 nullable exact precondition이다. 최초 manual PUT 또는 최초
suggestion accept는 required `expected_assignment_revision=null`로 assignment 부재를 관찰하고,
transaction은 부재에서 revision 1로의 전환을 conditional insert/CAS한다. Current assignment가 있는데
null을 보내거나 current가 없는데 non-null revision을 보내면 fixed stale conflict다. Sentinel revision과
read 뒤 무조건 insert를 사용하지 않는다. 같은 부재를 관찰한 concurrent first PUT/accept는 하나만
revision 1과 canonical audit를 commit한다.

Nullable current-state precondition은 자원마다 임의 sentinel로 재정의하지 않고 다음처럼 통일한다.

| Current state | `null`이 exact absence로 유효한 command | Existing non-null precondition이 필요한 command |
| --- | --- | --- |
| Taxonomy/profile-policy pointer | First publish, taxonomy-less 또는 current profile-policy가 없는 platform-default fallback resolver/override/reindex | Successor publish |
| Classification assignment pointer | First PUT, assignment가 없는 상태의 suggestion accept, fallback resolver를 사용하는 override/reindex | Current-validation, lock/unlock, existing assignment replacement |
| Processing profile override revision | First set | Change/clear |

Absence가 유효한 command도 field를 생략할 수 없고 current가 생긴 뒤 null precondition을 retry하면 fixed stale
conflict다. Existing state가 필수인 command는 null을 schema 또는 domain precondition에서 거부한다.

Current taxonomy pointer가 없는 Organization에서도 type-only assignment는 가능하다. 이때 request와
assignment snapshot의 taxonomy version은 `null`, topic set은 empty, primary topic은 `null`이어야 한다.
Current taxonomy가 존재하면 empty taxonomy version을 포함해 그 exact current version을 사용해야 하며
nullable precondition으로 이를 우회할 수 없다.
Null taxonomy로 확정된 empty-topic assignment 뒤 최초 taxonomy가 publish되면 assignment를 자동 변경하거나
invalid-topic `review_required`로 표시하지 않는다. Assigned snapshot이 current보다 뒤처진
`current_validation_required` 상태로 두고, explicit validation이 empty set과 source를 보존한 채
last-validated taxonomy ref만 전진시킨다. 이 상태는 topic axis만 resolver-ineligible이고 document-type axis의
freshness와 resolver eligibility를 바꾸지 않는다.
Assigned taxonomy가 prior published version인 empty-topic assignment 뒤 empty successor가 publish된 경우도
같다. 반대로 non-empty topic assignment는 candidate empty taxonomy에서 reference가 missing하므로
`review_required`다.

Assignment는 최초 결정에 사용한 taxonomy/document-type registry version과 마지막으로 유효성을
검증한 current version을 구분한다. Taxonomy publish는 assignment나 active artifact를 자동
변경하지 않는다. Stable ID가 current version에도 유효하면 explicit validation으로
`last_validated_*`를 전진시킬 수 있고, missing/deprecated이면 `review_required`로 남긴다. 과거
label/path는 최초 결정 version의 immutable snapshot으로 재현하고 current picker/display는 current
version의 bounded label을 사용한다.

Canonical materialization input hash가 달라져 새 canonical content revision이 생기면 existing assignment
revision과 provenance는 자동 변경하지 않되 axis별 content freshness를 다시 계산한다. `locked=true` axis는
명시적 manual lock의 carry-forward로 `review_recommended`를 표시하면서 hard filter, ranking과 profile mapping
입력으로 계속 사용할 수 있다. Unlocked `manual|accepted_suggestion|rule|fallback` axis는
`current_validation_required`이고 effective value는 review projection에 보존하지만 hard filter, ranking,
profile mapping과 materialized assignment-derived field에서 제외한다. `review_required`는 missing/deprecated
reference처럼 값 자체가 유효하지 않은 상태에만 사용한다. Resolver는 현재 content에서 eligible한 axis만
사용하고 없으면 general profile로 fail-safe fallback한다. 따라서 stale unlocked assignment만으로 preview나
reindex를 전면 차단하지 않지만, stale value를 새 artifact에 materialize하지 않는다. Existing active-ready
artifact는 새 ready artifact가 원자적으로 활성화될 때까지 retrieval-visible하다. Source generation이나
profile-only output ID만 바뀌고 canonical materialization input hash가 같으면 이 전이를 만들지 않는다.

Lock은 과거 assignment를 current content에서 검증한 것으로 소급 해석하지 않는다. Unlocked axis를 lock으로
전이하는 command는 target axis, non-null `expected_assignment_revision`과
`expected_canonical_content_revision_id`를 요구하고, application transaction이 current assignment와 canonical
content pointer를 함께 잠그거나 동등한 CAS로 다시 검증한다. 어느 snapshot이라도 다르면
`409 knowledge.classification_lock_snapshot_conflict`다. 실제 `false -> true` 전이는 target axis freshness가
`current`일 때만 허용한다. `current_validation_required`는 explicit current-validation을,
`review_required`는 current option을 사용한 complete assignment replacement를 먼저 요구한다. Invalid axis가
이미 locked이면 authority-reduction unlock 뒤 replacement 순서로 복구하며, write-only actor에게 unlock 우회를
허용하지 않는다. 그렇지 않으면
`409 knowledge.classification_lock_not_current`로 mutation과 audit 없이 닫는다. 이미 locked인 exact-state
request는 unchanged가 될 수 있다. Unlock은 authority를 낮추는 recovery command이므로 target axis와 current
assignment revision만 요구하고 stale content 또는 missing/deprecated reference 때문에 차단하지 않는다. 다만
Client content precondition 없이 current assignment와 canonical content pointer를 같은 transaction에서
직렬화하고, 성공한 complete revision의 freshness/resolver projection을 그 server current state로 재계산한다.

Explicit current-validation은 assignment replacement와 다른 command다. Current assignment,
canonical content revision, registry/taxonomy pointer와 `expected_assignment_revision`을 같은 transaction에서
검증하고 fixed `validation_reason_code`를 요구한다. Server는 last-validated snapshot과 current snapshot을 비교해
변경 차원을 `content|taxonomy|document_type_registry` 집합으로 계산한다. 정확히 하나만 바뀌면 각각
`content_reviewed|taxonomy_updated|registry_updated`, 둘 이상이 바뀌면 `combined_review`만 허용한다. Content가
달라졌는데 content-confirming reason이 아니면 `knowledge.classification_content_confirmation_required`, 그 밖의
reason/change-set mismatch는 `knowledge.classification_validation_reason_mismatch`로 mutation 없이 닫는다.
Changed dimension이 없으면 reason을 해석하거나 저장하지 않고 `unchanged`로 종료한다. 이 문서에서
content-confirming validation은 server-computed set에 `content`가 포함된 경우만 뜻하며 effective KB
`content_read`와 applicable current display/raw policy를 추가로 요구한다.
Source-managed KB는 Organization manager도 우회할 수 없는 fresh source authorization/display gate를 content 노출과
validation commit 전에 다시 통과하고 authoritative revision/watermark를 mutation과 직렬화한다. 모든 stable ID가
current version에서도 유효하면 effective set, axis별 source/lock,
`effective_from_*`와 최초 assigned snapshot은 보존한 새 assignment revision으로
`last_validated_*`만 전진시키고 별도 `validation_source=manual_confirmation`과 reason을 canonical audit에
함께 저장한다. 이는 원래 assignment authority를 manual로 바꾸지 않으면서 current content를 사람이 확인했다는
provenance다. 성공한 validation은 current resolver
state와 `reindex_required` projection을 다시 계산하지만 Processing Decision Manifest, reindex job 또는
active pointer 변경을 자동 시작하지 않는다. 이미 current version까지 검증된 assignment는
`unchanged`이고 revision/audit/reindex projection을 바꾸지 않는다. Missing/deprecated reference는
`review_required`를 반환하되 last-validated ref를 전진시키거나 assignment를 변경하지 않는다.

Target cutover 전 여러 독립 Document가 하나의 legacy KB에 존재하면 KB ID만으로 assignment를
변경하지 않는다. Exact legacy document와 version을 요구하며 모호한 대상은
`migration_required`로 fail-closed한다.

AI와 deterministic classifier의 결과는 authoritative assignment가 아닌 suggestion이다.
Suggestion은 exact canonical content revision/hash, nullable taxonomy pointer/document-type registry version과
tagged `generator_kind=deterministic_rule|ai_classifier` provenance에 종속된다. 두 kind 모두 immutable
`generator_contract_ref`를 요구한다. Deterministic candidate는 approved rule-set/version을 가리키며 provider model,
prompt-template, calibration과 confidence field를 저장하지 않는다. AI candidate는 classifier policy,
prompt-template contract, model catalog/effective model, calibration revision/state와 bounded confidence bucket을
요구한다. Kind와 맞지 않는 fake/non-applicable reference를 만들지 않는다. Current taxonomy가 없으면 suggestion도
null taxonomy + empty topic set의 type-only candidate만 만들 수 있다. Content 또는 대상
registry/taxonomy가 바뀌면 null-to-version 전환을 포함해 아직 `suggested`인 candidate는 `expired`가 되며
새 content에 승계하지 않는다. 이미 terminal인 accepted/rejected/abstained outcome은 다시 쓰지 않고,
accepted assignment의 current-validation 상태와 provenance를 별도로 관리한다.

Suggestion state는 `suggested`, `accepted`, `rejected`, `abstained`, `expired`를 구분한다. Review 목록은
retention 범위의 operational row를 무제한 반환하지 않고 state-bound opaque keyset cursor와 stable
`created_at DESC, suggestion_id DESC` tie-break를 사용한다.

Manual assignment `PUT`은 partial topic merge가 아니라 complete effective set을 제출하되, non-empty
`manual_axes`로 authority/value를 바꿀 `document_type|topics` axis를 명시한다. 선택한 axis만 unlocked 상태에서
manual replacement 또는 takeover가 되고, 선택하지 않은 axis는 request value가 current effective value와
일치해야 하며 server가 value/source/lock과 최초 assigned snapshot을 그대로 보존한다. Current assignment가
없으면 보존할 axis가 없으므로 두 axis를 모두 선택해야 한다. Empty/duplicate/unknown axis, first create의
incomplete selection과 unselected-axis value mismatch는 assignment/audit 없이 fixed validation 또는 conflict로
닫는다. 이 command도 complete assignment revision 하나를 원자적으로 만들며 Client가 omitted topic을 기존
set에 암묵적으로 merge하게 하지 않는다.

Manual assignment wire는 Client가 정한 `reason_code`나 free-text reason을 받지 않는다. Server는 first create
또는 selected axis 중 하나라도 normalized value가 바뀌면 fixed `manual_assignment`, selected value는 모두
같지만 non-manual source가 `manual`로 바뀌면 fixed `manual_takeover`를 파생한다. Value와 authority가 함께
바뀌는 경우에는 `manual_assignment`가 우선한다. 이 두 값 외의 reason을 저장하지 않고 canonical audit도
server-derived reason만 사용한다. `reason_code`를 포함한 unknown top-level request field는 DB 조회와
assignment/audit mutation 전에 `422 knowledge.classification_request_invalid`로 거부해 사용자 입력, secret 또는
PII가 audit reason으로 들어가지 않게 한다.

Suggestion candidate는 제안하는 axis를 명시한다. Accept command는 candidate axis의 non-empty subset을
선택하고, 선택하지 않은 axis의 effective value/source/lock을 보존한 complete assignment를 server가
materialize해 한 revision으로 commit한다. 이는 server-owned immutable candidate를 선택하는 command이며
Client-supplied partial assignment merge가 아니다. Accept는 nullable current taxonomy pointer를 포함한
candidate snapshot, current axis별 authority/lock과 required nullable `expected_assignment_revision`을 같은
transaction에서 다시 검증한 뒤에만 새 effective assignment revision을 만든다.
Selected subset accept도 suggestion row 전체를 terminal `accepted`로 전이하고 immutable provenance에
`accepted_axes`를 기록한다. 선택하지 않은 proposed axis는 적용되지 않은 채 폐기하며 별도 reviewable child
suggestion으로 남기거나 이후 retry에서 추가 적용하지 않는다.
AI가 직접 security classification, permission,
retention, profile activation 또는 active index pointer를 변경하지 않는다.

Suggestion accept는 mutation 전에 같은 `expected_assignment_revision`과 `accepted_axes`를 사용하는
server-owned accept impact preview를 필수로 거친다. Preview는 immutable candidate를 current assignment에
hypothetically 적용하고 current canonical content, nullable taxonomy, registry, profile policy/catalog,
override, resolved scoped profile ref, nullable current Processing Decision Manifest와 nullable active Artifact Build
Manifest를 다시 resolve해 axis별 value/authority change,
`profile_resolution_changed`, `reindex_required`와 `active_ready_available` safe boolean만 반환한다. Axis effect는
value가 바뀌면 authority도 함께 바뀌는지와 무관하게 `value_changed`, value는 같고 source/authority만 바뀌면
`authority_changed`, 둘 다 같으면 `unchanged`다. 선택하지 않은 axis도 `unchanged`다. 이 우선순위로 한 상태가
두 의미를 갖지 않게 한다. Organization, KB, actor, suggestion/state, candidate snapshot, accepted axes와 위
resolver vector에 더해 current decision ref 및 resolver/materialization input fingerprint, active artifact
ref·generation·availability 및 build materialization/integrity fingerprint를 최대 10분 opaque
`accept_preview_revision`의 server-side validation state에 bind한다. Raw content, hidden identity, exact
cost/count와 내부 fingerprint는 response 또는 token plaintext로 반환하지 않는다. Token은 capability가 아니고
response는 `no-store`다.

Accept request는 `expected_accept_preview_revision`을 함께 보낸다. Server는 fresh source/display gate를 candidate와
token lookup보다 먼저 재평가하고 외부 authorization session을 닫은 뒤 bounded revision/watermark만 mutation
transaction에 전달한다. 같은 transaction에서 token scope, expiry, 전체 vector와 authorization
revision/watermark를 commit 직전에 다시 검증한다. Candidate, accepted axes, assignment,
content/taxonomy/registry, resolver/materialization input, current decision 또는 active artifact identity/state가
바뀌면 safe boolean 결과가 우연히 같아도 assignment/outcome/audit 없이
`409 knowledge.classification_suggestion_accept_preview_stale`로 닫는다. Fresh authorization을 통과한 exact
terminal retry는 terminal outcome에 저장한 non-reversible accept request fingerprint가 같을 때 preview expiry보다
먼저 기존 safe result를 반환하고 audit/reindex intent를 반복하지 않는다. 다른 terminal request는 기존 outcome을
바꾸지 않는다. Raw preview token, digest와 내부 fingerprint는 audit metadata에 저장하지 않는다.
Accepted assignment revision은 suggestion operational row와 독립된 immutable safe provenance snapshot을
가진다. Snapshot에는 opaque suggestion ref, `accepted` outcome, canonical content/taxonomy/registry refs,
accepted axes와 tagged generator provenance를 포함한다. Deterministic snapshot은 rule-set
`generator_contract_ref`만, AI snapshot은 classifier policy, prompt-template contract, model catalog/effective model,
calibration revision/state와 bounded confidence bucket을 포함한다. Raw prompt/completion/rationale, raw model score, provider response,
credential과 source content는 포함하지 않는다. Suggestion row purge는 이 snapshot이나 canonical accept
audit를 cascade 삭제하지 않는다.

Reject는 assignment를 변경하지 않는 별도 terminal command다. Current `suggested` row와 fresh source
authorization을 다시 검증하고 `rejected` outcome과 bounded fixed reason 및 canonical
`knowledge.classification_suggestion.rejected` audit를 같은 transaction에서 확정한다. Audit failure는 outcome을
rollback하고 terminal retry는 새 audit를 만들지 않는다. Candidate label/content, raw rationale와 score는 audit에
저장하지 않는다.

유효한 manual assignment는 해당 axis에서 lock 여부와 무관하게 accepted suggestion보다 높은
authority다. 따라서 suggestion accept는 선택한 axis의 unlocked manual assignment도 덮어쓰지 않고
conflict로 끝난다. 선택하지 않은 manual axis는 그대로 보존할 수 있다. 사용자가 blocked candidate를
의도적으로 채택하려면 complete set과 해당 candidate axis를 `manual_axes`로 명시한 manual assignment
replacement를 제출해야 한다. 선택한 axis만 manual authority가 되고 선택하지 않은 axis의 source/lock은
보존된다. Suggestion accept가 manual authority를 암묵적으로 해제하거나
낮추는 옵션은 제공하지 않는다.

Current expected revision, normalized complete effective set과 selected `manual_axes`가 current state와
일치하고 선택한 axis의 authority/source가 이미 `manual`이면 assignment PUT은 current result를
`unchanged`로 반환하고 revision, audit와 reindex intent를 만들지 않는다. 선택한 axis의 source가 rule,
`accepted_suggestion`, fallback 등 non-manual이면 같은 value를 명시적으로 PUT해도 그 axis의 manual
takeover라는 authoritative change이므로 새 complete assignment revision과 canonical audit를 확정한다.
선택하지 않은 non-manual 또는 locked axis는 source/lock을 보존한다. 이 authority-only transition은 current
resolver state와 `reindex_required` projection을
다시 계산하지만 reindex job을 자동 시작하지 않는다. 이후 명시적 admission에서 materialization input이
같으면 `satisfied_existing`으로 수렴한다.
Expected revision이 stale하면 desired set이 우연히 같아도 `409`다. 이미 terminal인 suggestion review
재시도는 기존 terminal result를 반환하고 새 assignment revision, audit 또는 reindex intent를 만들지
않는다.

### 4. Manual authority와 lock이 자동 판단보다 우선한다

Axis별 authority 우선순위는 다음과 같다.

1. 유효한 manual lock
2. 유효한 manual assignment
3. 사람이 accept한 AI suggestion
4. 승인된 deterministic rule-derived assignment
5. unclassified/unknown fallback

AI suggestion은 선택한 axis의 manual assignment 또는 lock을 해제하거나 덮어쓰지 못한다. Locked axis는 새
canonical content revision에서도 유지하되 `review_recommended`를 표시한다. Suggestion accept,
unlock과 profile override는 optimistic concurrency revision을 요구하며 last-write-wins로 topic
set 또는 lock을 덮어쓰지 않는다.

Effective classification projection은 하나의 합성 `source`로 authority를 축약하지 않는다. Document type과
topics 각각의 source/lock/review state를 반환하고 mutation, audit와 UI가 같은 axis별 authority를 사용한다.

Raw model score는 독립 holdout calibration 전까지 correctness probability가 아니다.
Uncalibrated score는 자동 action threshold, 보안 판단 또는 사용자에게 정확도처럼 표시하는 데
사용하지 않는다. Classifier는 허용된 ID만 제안하며 low-confidence, out-of-taxonomy와 ambiguous
case에서 abstain할 수 있어야 한다.

### 5. 권한과 organization 경계를 기존 RBAC 위에 명시한다

V1 target authority는 다음과 같다.

| 작업 | 요구 권한 |
| --- | --- |
| Effective classification 조회 | KB `read` 또는 더 강한 권한 |
| Unlocked type/topic 변경, suggestion review, profile preview/reindex 요청 | KB `write` 또는 Organization manager |
| Classification lock/unlock, explicit profile override | KB `manage` 또는 Organization manager |
| Organization taxonomy와 profile mapping policy author/publish | Organization manager |

KB-scoped 행의 Organization manager 대안은 이 ADR이 새로 부여하는 권한이 아니라 ADR-0034의 모든 KB resource
action override를 적용한 것이다. Organization manager에게 별도 KB grant가 없어도 같은 active organization의
해당 행은 허용하지만 source authorization, display/raw policy와 ownership-first hiding은 우회하지 않는다.
Source-managed KB의 모든 KB-scoped classification/resolver read와 mutation은 parent ownership과 위 KB action을
확인한 뒤 assignment, lock, override 또는 resolver-specific row를 조회·projection·mutation하기 전에 fresh
requester source authorization과 applicable display policy를 통과해야 한다. Effective classification GET/options,
assignment PUT, current-validation, lock/unlock, explicit profile override GET/options/set/clear와 resolve-preview가
이에 포함된다. Missing, revoked, stale 또는 denied gate는 Organization manager에게도 ADR-0017의
`404 resource.hidden`으로 닫고 assignment/override 존재, stable ID, axis authority/lock, security classification과
resolver 상태를 반환하거나 변경하지 않는다. Mutation은 외부 authorization session을 종료한 뒤 bounded
revision/watermark만 transaction에 전달하고 commit 직전에 current KB authority와 함께 다시 검증한다. Revoke가
serialization winner이면 assignment/lock/override, impact snapshot, canonical audit와 reindex projection을 모두
만들지 않는다. Manual KB에는 이 source gate를 적용하지 않는다.
`catalog_manage` 등 위임된 Knowledge domain action은 이 override와 같지 않다.

다른 organization resource와 hidden target은 ADR-0010의 safe hiding을 적용한다. Scope 안 권한
부족은 기존 Knowledge API permission contract를 따른다. ADR-0034의 `catalog_manage`,
`permission_delegate`, `lifecycle_manage`, `sync_manage`를 taxonomy/profile publish 권한으로
확대 해석하지 않는다. 향후 taxonomy steward 위임이 필요하면 별도 domain action과
self-escalation/audit 정책을 승인한다.

Organization 또는 KB 하위 reference는 parent scope 안에서 ownership을 먼저 확인한 뒤 lifecycle,
authority와 semantic validation을 수행한다. Cross-organization taxonomy/policy version,
assignment/suggestion, organization-owned profile/override와 reindex reference는 같은 safe `404`
경계를 사용한다. Platform document type/profile registry reference는 cross-organization resource로
취급하지 않고 current registry visibility와 selectable state를 fixed error contract로 검증한다.

Source-managed content를 classifier에 전달하려면 KB 권한과 별도로 fresh source
authorization, display/raw policy, provider egress와 redaction gate를 통과해야 한다. 이 gate는 suggestion
생성에만 적용되는 일회성 검사가 아니다. Suggestion list의 각 page와 suggestion accept-preview/accept/reject는
parent KB scope 및 RBAC를 확인한 뒤 candidate row를 조회하거나 projection/mutation하기 전에 fresh source
authorization과 display policy를 다시 평가한다. Source authorization revision/watermark가 review 중
바뀌면 commit 직전에 다시 검증해 fail-closed하며 cursor나 suggestion ID를 capability로 사용하지 않는다.
Denied/revoked/stale source authorization은 ADR-0017의 `resource.hidden` safe `404`로 닫고 candidate
identity, state, count, confidence와 terminal outcome을 반환하거나 변경하지 않는다.
Source-managed KB의 모든 current-validation은 위 fresh source/display gate와 commit 직전 revision/watermark
재검증을 적용한다. Changed dimension에 content가 포함되면 추가로 effective `content_read`와 applicable current
display/raw policy를 요구한다. Taxonomy/registry-only validation은 `content_read`를 요구하거나 content를
확인했다는 provenance를 만들지 않지만 source-managed resource hiding gate는 생략하지 않는다.

### 6. Profile resolution은 immutable policy와 general fallback을 사용한다

Chunking profile revision은 `platform` 또는 `organization` scope의 immutable catalog resource다.
Organization mapping policy와 explicit override는 active organization이 소유한 published revision 또는
platform이 승인한 published revision만 참조할 수 있고 다른 organization profile을 참조하지 않는다.
Platform registry reference는 tenant resource가 아니므로 cross-organization hiding과 구분한다.

Organization profile catalog는 stable opaque profile identity와 mutable draft/immutable published revision을
분리한다. Organization manager만 identity/first draft 생성, bounded list/detail, successor draft 생성,
draft PATCH/validate/publish와 published revision deprecate를 수행한다. Draft mutation은
`draft_revision` CAS를, publish/deprecate는 Organization-scoped monotonic
`organization_profile_catalog_revision` CAS를
사용한다. Draft create/PATCH와 해당 canonical audit는 같은 transaction이지만 catalog revision을 바꾸지 않고,
published selectability를 바꾸는 publish/deprecate와 canonical audit만 같은 transaction에서 catalog revision을
전진시킨다.
First-create command의 body는 exact empty JSON object `{}`다. Missing body, non-object 또는 어떤 field라도
포함한 object는 DB 조회 전에 `422 knowledge.processing_profile_invalid`로 거부한다. 성공하면 stable identity와
`base_revision_id=null`, `draft_revision=0`, `config=null`인 incomplete first-draft shell 및
`knowledge.processing_profile_draft.created` audit를 한 transaction에서 만들고 current Organization catalog
revision을 반환한다. Draft는 selectable하지 않으므로 이 create 자체는 catalog revision을 전진시키지 않는다.
첫 successful PATCH가 complete `processing_profile_schema_v1` config를 저장해야 하며 null/incomplete config는
validate/publish할 수 없다.
Successor draft command는 request의 exact `base_revision_id`를 요구한다. Base는 path의 same-organization
profile identity에 속한 immutable `published` revision이어야 하며 server는 그 revision의 normalized config를
복제하고 새 draft에 immutable lineage로 `base_revision_id`를 기록한다. Cross-organization 또는 다른 profile
identity의 base는 ownership-first safe `404`, same-scope draft/deprecated/non-published base는
`409 knowledge.processing_profile_successor_base_invalid`로 draft, catalog revision과 audit를 만들지 않는다.
Tokenizer/version, token chunk size/overlap, boundary/parser, retrieval representation, embedding/model catalog
reference와 algorithm version은 allowlisted schema 및 compatibility 검증을 통과해야 한다. Credential, raw
provider config와 arbitrary executable parser option은 profile에 저장하지 않는다.

Published configuration은 수정하거나 hard-delete하지 않는다. Deprecation은 별도 selectability lifecycle
event이며 current profile mapping policy 또는 current KB override가 직접 참조하면 먼저 successor policy를
publish하거나 override를 clear하기 전까지 `409 knowledge.processing_profile_in_use`로 거부한다. Historical
policy/override, Decision/Build Manifest와 active-ready artifact reference는 provenance를 위해 유지하며
deprecation 자체가 reindex나 pointer swap을 시작하지 않는다. Deprecated revision은 신규 policy/override
option과 existing-artifact satisfaction 후보에서 제외하지만 기존 active-ready artifact는 replacement가
ready가 될 때까지 계속 검색 가능하다. Platform profile lifecycle은 platform registry owner가 관리하고
Organization API가 변경하지 않는다.

Platform profile catalog도 별도 monotonic `platform_profile_catalog_revision`과 selectability lifecycle을
가진다. Organization API는 이 revision과 approved published option을 read-only로 조회한다. Profile policy
validate/impact preview/publish는 Organization과 Platform catalog revision을 각각 반환하고 exact expected 값으로
요구한다. Policy publish는 두 catalog serialization row를 deterministic order로 잠그거나 동등한 serializable
CAS를 사용해 rule/general fallback reference를 재검증한다.

Organization 또는 Platform profile deprecate와 해당 profile을 새로 참조하는 policy publish, override mutation
또는 Platform default pointer 변경은 적용되는 catalog serialization boundary와 exact catalog revision을 공유한다.
Platform deprecate도 platform catalog boundary 아래 current policy/override reference와 current default pointer를
검사한다. 따라서 deprecate가 reference 부재를 확인하는 사이 새 current reference/default가 commit되거나,
deprecated revision을 가리키는 reference가 뒤늦게 commit되는 write skew를 허용하지 않는다. Deprecate가 먼저
commit되면 stale policy/override/default mutation은 전체 rollback하고, 새 reference가 먼저 commit되면
deprecate는 in-use로 닫힌다.

Platform catalog state는 exact approved published/selectable Platform profile revision을 가리키는 non-null
`platform_default_profile_revision` pointer를 소유한다. 이 pointer는 profile control plane을 Organization에
노출하기 전에 초기화해야 하고 mutable profile config나 `latest` lookup이 아니다. Pointer 변경은 platform
registry owner/system actor만 수행하고 required exact expected Platform catalog revision을 사용한다. Platform
catalog serialization boundary에서 current target selectability를 다시 검증하고 pointer,
`platform_profile_catalog_revision` 및 canonical `knowledge.processing_profile_default.changed` audit를 같은
transaction에서 확정한다. Audit failure와 stale CAS는 pointer/catalog 전체를 rollback한다. Current default
target은 pointer를 다른 selectable revision으로 옮기기 전에는 deprecate할 수 없다. Organization API는
pointer와 revision의 safe read-only projection만 제공한다.

Current Organization profile-policy pointer가 null이면 valid explicit override가 먼저이고, override가 없을 때
resolver는 이 exact Platform default revision을 `platform_default` source로 선택한다. Nullable policy pointer,
default ref와 Platform catalog revision을 resolver fingerprint와 preview/admission token에 함께 bind한다. Current
policy가 존재하면 Platform default pointer를 그 policy의 fallback처럼 혼합하지 않고 아래 policy rule/general
순서를 사용한다. Null-policy이고 valid explicit override가 없어 default branch를 실제 선택해야 하는데 target이
missing/deprecated/incompatible이면 preview, reindex admission과 default가 필요한 override clear를
`503 knowledge.processing_platform_default_unavailable`로 fail-closed하고 기존 active-ready artifact는 유지한다.
Selectable target을 지정하는 explicit override set/change는 이 상태의 recovery path로 허용하되 target과 전체
snapshot을 다시 검증한다. 어떤 경로도 mutable `latest`, environment default 또는 legacy character chunk
setting으로 조용히 fallback하지 않는다.
Override GET/options는 recovery를 위해 `200` safe projection을 유지하고 effective resolution을 generic
`platform_default_unavailable`, `resolved_profile_revision_ref`를 null로 반환하며 current unavailable vector를 bind한
opaque resolver revision을 발급한다. 이 read token도 capability가 아니고 set은 commit 시 current vector를 다시 검증한다.

Document type/topic에서 profile revision으로 가는 mapping은 versioned organization policy다.
동일 specificity의 충돌은 publish 전에 거부한다. Platform general profile은 모든 published policy의 필수
fallback이고 Organization general profile은 선택 사항이다. Current policy가 존재할 때 resolver는 다음 순서로 하나의 exact profile
revision을 선택한다.

1. Valid explicit manual profile override
2. Document type + primary topic rule
3. Document type rule
4. Primary topic rule
5. Organization general profile
6. Platform general profile

Deprecated, missing 또는 review-required topic은 mapping 입력에서 제외한다. Unknown, unclassified, stale
mapping, 허용된 rule이 없거나 Organization general이 없는 문서는 다음 유효한 general rule 또는 필수 Platform
general profile로 fail-safe fallback한다. 다만 명시적 manual override가 missing, deprecated 또는
incompatible이면 이를
조용히 무시하지 않고 `review_required` conflict로 preview/reindex를 차단하며 기존 active ready
version을 유지한다. AI가 profile ID를 직접 선택하지 않는다.

V1 token chunk config는 `processing_profile_schema_v1` safety envelope을 snapshot한다.
`chunk_size_tokens`는 boolean이 아닌 정수 `64..8192` inclusive이고, `chunk_overlap_tokens`는 boolean이 아닌
정수 `0..floor(chunk_size_tokens / 2)` inclusive다. 따라서 overlap stride는 chunk size의 절반 이상이고
overlap 때문에 chunk 수가 비정상적으로 폭증하지 않는다. String, float, null, 음수와 범위 밖 값은 coercion
없이 거부한다. Selected tokenizer/parser/representation/embedding capability의 current published limit가 더
작으면 profile validate/publish는 그 더 작은 limit를 적용하며 catalog capability가 이 V1 envelope을 넓힐 수
없다. 이 범위는 품질 최적 기본값이 아니라 transport/processing safety bound이고, document type별 production
default는 Decision 10의 confirmatory evidence를 별도로 요구한다. Bound 또는 coercion을 바꾸려면 기존 published
revision을 재해석하지 않고 새 profile schema version과 ADR을 추가한다.

Profile revision은 tokenizer와 version, chunk unit/size/overlap, boundary strategy, parser,
representation, embedding reference와 algorithm version을 재현 가능하게 고정한다. 기존
`documents.chunk_size/chunk_overlap` character 값은 조용히 token 값으로 재해석하지 않고 legacy
adapter input으로만 읽는다.

`processing_profile_policy_v1` authoring document는 Draft PATCH request의 required `policy` field에 담기는
complete policy document다. Draft create wire envelope는 required `policy`만, PATCH wire envelope는 required
`expected_draft_revision`과 `policy`만 허용한다. Create는 request document 그대로 `draft_revision=0`을 만들고
server default와 omitted field merge를 사용하지 않는다. `policy` object top-level은 required
`policy_schema_version`, raw `rules` array, required nullable
`organization_general_profile`과 `platform_general_profile`만 허용한다. `rules` raw array는 duplicate 제거 전에
최대 2,000개다. 각 rule의
normalized matcher는 다음 discriminated union 중 하나다.

- `type_primary`: `document_type_id`와 `primary_topic_id`가 모두 required
- `type`: `document_type_id`만 required
- `primary`: `primary_topic_id`만 required

각 rule의 `profile_revision_ref`는 `catalog_scope=organization|platform`과 exact
`profile_revision_id`만 가진다. General fallback도 같은 reference shape를 사용하되 Organization general은
`organization`, Platform general은 `platform` scope만 허용한다. 두 general field는 draft recovery shape를
고정하기 위해 항상 존재하며 incomplete draft에서는 null일 수 있다. Validate/impact-preview/publish는
non-null Platform general을 필수로 요구하고 Organization general null은 유효하다. PATCH는 위 complete document
replacement이며 omitted rule merge나 partial operation을 허용하지 않는다. Unknown field, matcher kind와 맞지
않는 field, normalized duplicate matcher, cross-organization/deprecated/incompatible reference와 raw 2,001번째
rule은 mutation 없이 거부한다. 이 cap이나 union을 바꾸려면 새 policy schema version을 추가한다.

Profile mapping policy draft도 taxonomy draft와 동일한 `draft_revision` CAS를 사용한다. PATCH,
validate, impact preview와 publish는 exact draft revision을 요구하고 publish는 current policy pointer
CAS를 함께 검증한다.

Profile-policy impact preview는 선택적 estimate가 아니라 모든 profile-policy publish의 admission
precondition이다. Request는 exact `expected_draft_revision`을 요구하고 response는 최대 10분 수명의 opaque
`impact_preview_revision`, expiry, current policy/taxonomy/registry, Organization/Platform profile catalog,
`assignment_impact_snapshot_revision`과 `profile_policy_impact_bucket_v1` contract version을 반환한다. Token은
Organization/actor/candidate/draft와 이 전체 snapshot에 bind된 non-capability이며 response는 `no-store`다.

`profile_policy_impact_bucket_v1`은 같은 authoritative snapshot에서 두 내부 count를 각각 계산하고
`none=0`, `small=1..10`, `medium=11..100`, `large=101+`로 독립 변환한다.

- `affected_resolution_count`는 같은 Organization의 processing-eligible active KB/document를 하나씩 센다.
  Current resolver와 candidate policy resolver를 같은 current assignment 또는 assignment 부재, taxonomy/registry,
  catalog, override와 canonical input에서 평가하고 `effective_resolution_status`, `resolution_source` 또는
  `resolved_profile_revision_ref`가 달라지는 target만 포함한다. Assignment가 없는 target도 current Platform default 또는
  current policy general과 candidate policy general fallback으로 resolve할 수 있으므로 포함한다. Valid explicit
  override가 두 resolver에서 계속 우선하거나 invalid override가 계속 `review_required`로 차단해 candidate policy가
  선택되지 않는 target은 제외한다.
- `reindex_candidate_count`는 affected subset 중 current active-ready artifact가 있는 target만 평가한다. Current
  Processing Decision과 비교 가능한 `materialization_input_fingerprint`가 있으면 candidate fingerprint가 다른
  target만 세고, active-ready legacy artifact는 있지만 current decision 또는 비교 가능한 fingerprint가 없으면
  동일 materialization을 증명할 수 없으므로 보수적으로 포함한다. Active artifact가 없는 affected target은
  affected bucket에는 포함하되 reindex bucket에서는 제외한다.

Archived/deleted KB/document 또는 processing-ineligible target은 두 count에서 제외한다. Artifact availability
`purging|purged`는 current/candidate resolver 차이를 없애지 않으므로 processing-eligible active target이면
affected count에는 포함할 수 있지만 active-ready artifact가 아니므로 reindex candidate에서는 제외한다.
Publish는 response의 exact
compatibility snapshot과 `expected_impact_bucket_contract_version`, `expected_impact_preview_revision`, literal
`impact_acknowledged=true`를 요구한다. Missing/expired/wrong actor·scope·candidate, changed snapshot 또는 contract
version mismatch는 `409 knowledge.processing_profile_policy_impact_preview_stale`로 policy/current pointer/audit를
변경하지 않는다. `assignment_impact_snapshot_revision`은 token에 bind된 server-owned precondition이며 별도
publish request field로 받지 않고 current revision과 transaction 안에서 비교한다. Valid token을 확인한 뒤 commit
CAS에서 경합을 잃은 경우는 기존
`knowledge.processing_profile_policy_publish_snapshot_conflict`다. Exact count, hidden identity와 token 원문은
response/audit에 넣지 않고 preview/publish는 assignment 변경이나 reindex job을 자동 시작하지 않는다.

Explicit override set/clear는 override row만 바꾸는 독립 mutation이 아니라 resolver-control mutation이다.
Override target은 profile-policy와 같은 strict scoped reference인
`profile_revision_ref={catalog_scope: organization|platform, profile_revision_id}`를 사용한다. Ref 자체가 null 또는
object가 아니거나, `catalog_scope`가 missing/unknown/non-string이거나, `profile_revision_id`가 empty/non-string이거나,
bare revision ID 또는 extra field가 있으면 repository 조회 전에
`422 knowledge.processing_profile_override_invalid`다. Organization scope는 active Organization 소유 published/selectable revision을
ownership-first로 resolve하고 Platform scope는 approved published/selectable registry revision만 허용한다. 따라서
두 catalog에 같은 opaque revision ID가 있어도 `catalog_scope`가 대상을 결정하며 cross-organization Organization
reference는 safe `404`로 숨긴다. Override read는 nullable current mapping-policy version을 safe precondition으로
반환한다. Set/clear request는 required nullable `expected_assignment_revision`, required nullable `expected_profile_policy_version`,
`expected_override_revision`과 organization, KB, actor, current canonical content, nullable assignment,
registry/nullable taxonomy, nullable mapping policy, nullable override, Organization/Platform profile
catalog/selectability와 resolved exact scoped `profile_revision_ref`를 bind한 opaque
`expected_override_resolver_revision`을 요구한다. Opaque token이
명시적 nullable pointer precondition을 대체하지 않는다. Token은 capability가 아니며 다른 scope/actor에서
재사용할 수 없다. Server는 set/clear transaction에서 전체 resolver input vector를 다시 읽고 pointer/revision row
lock, conditional CAS 또는 동등한 serializable validation으로 mutation과 `reindex_required` projection을
같은 snapshot에 묶는다. Content, assignment, taxonomy/registry, policy, catalog/selectability 또는 override
중 하나라도 바뀌면 override와 audit를 commit하지 않고 fixed stale conflict로 닫는다. Set audit은 새 target의
safe scoped ref만 기록한다. Clear audit은 이전 override가 caller에게 valid하고 visible할 때만 그 safe scoped ref를
기록하고, missing/deprecated/incompatible/cross-organization 등 generic unavailable state의 recovery에서는
KB-owned override revision과 fixed unavailable state만 기록해 hidden target profile identity를 노출하지 않는다.

### 7. Profile 변경은 create-only staging과 atomic activation을 사용한다

Processing 입력은 산출 staging `DocumentVersion` ID가 아니라 독립된 canonical content revision이다.
Canonical content revision은 KB/document scope, canonicalization contract와 redacted canonical bytes,
structure 및 materialization에 사용되는 safe content metadata의
`canonical_materialization_input_hash`를 고정한다. Source sync/document generation은 lineage ref로
별도 보존하고 canonical materialization input이 같으면 새 content revision을 만들지 않는다.

정책 판단 provenance와 artifact build provenance는 다음 immutable record로 분리한다.

- **Processing Decision Manifest**: canonical content revision ref, exact resolver input revision vector,
  resolution source/profile과 아래 두 input fingerprint를 고정한다.
- **Artifact Build Manifest**: canonical materialization input hash, immutable parser/chunking/
  representation/embedding configuration, materialized assignment-derived field, output artifact refs와
  생성 후 integrity hash를 고정한다.
- **Decision Satisfaction**: Decision Manifest가 기존 또는 신규 Artifact Build Manifest로 충족됐음을
  append-only로 연결한다. Current decision pointer와 active artifact pointer는 별도이며 기존 artifact
  재사용 시 decision pointer만 바꿀 수 있다.

Input fingerprint와 output integrity는 다음처럼 구분한다.

- `resolver_input_fingerprint`: assignment, current document-type registry/taxonomy, mapping policy,
  nullable override, Organization/Platform profile catalog/selectability와 resolved exact scoped profile ref를 고정해 stale
  finalization을 차단하고 정책 판단을 재현한다.
- `materialization_input_fingerprint`: job admission 전에 canonical materialization input hash와 실제
  output을 결정하는 immutable configuration 및 normalized materialized assignment-derived field를
  고정해 `reindex_required`를 판단한다. Canonical revision ID/generation, output DocumentVersion/index
  ID, display label과 생성 결과 bytes는 포함하지 않는다.
- `artifact_integrity_hash`: 생성 후 normalized chunk/representation/index manifest와 embedding
  completeness를 검증하는 output hash다. Job identity, dedupe 또는 reindex admission 입력이 아니다.

Durable job의 동일성/dedupe는 canonical content revision ref,
`resolver_input_fingerprint`와 `materialization_input_fingerprint`를 함께 사용한다. Materialization
fingerprint만 같고 resolver input이나 canonical revision이 다른 active job을 같은 current decision으로
재사용하지 않는다.

Processing Decision Manifest의 logical identity도 KB/document, canonical content revision ref와 두 input
fingerprint tuple이다. 동일 decision이 이미 current면 admission은 `unchanged`이고 새 manifest,
satisfaction 또는 job을 만들지 않는다. Durable idempotency retry와 concurrent admission은 같은 logical
decision/satisfaction으로 수렴한다.

Reindex transport idempotency는 body field가 아니라 required `Idempotency-Key` HTTP header 하나만 사용한다.
Header name은 HTTP 규칙대로 case-insensitive지만 값은 ASCII lower-case hyphenated UUID의 canonical 36자
representation `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`와 정확히 같아야 한다. Nil UUID, upper-case, leading/trailing
whitespace, braces/URN, malformed/다른 길이, comma-joined 값, duplicate header와 missing header는 trim,
case-fold 또는 coercion 없이 `422 knowledge.processing_idempotency_key_invalid`로 거부한다. Body의
idempotency field도 허용하지 않고 같은 fixed code로 거부한다.

Server는 검증된 canonical ASCII 36 byte의 SHA-256 digest만 organization/KB/actor/operation receipt key로
저장하고 raw header를 response, application/access log, audit와 trace에 남기지 않는다. Canonical request
digest는 idempotency header와 분리된 server-owned typed request tuple로 계산한다. 따라서 same key/exact tuple만
replay하고 same key/different tuple은 `knowledge.processing_idempotency_conflict`이며 다른 textual UUID
representation을 같은 key로 정규화해 받아들이지 않는다.
Reindex admission은 job 생성 여부와 무관하게 durable Processing Admission Receipt를 남긴다. Receipt는
organization/KB/actor/operation scope, protected idempotency-key digest, canonical request digest와
`unchanged/satisfied_existing/job_created/job_reused` safe result ref를 저장한다. Parent ownership과 current
KB `write` authorization 및 applicable fresh source authorization을 먼저 확인한 뒤 exact receipt replay를
조회하며, 같은 key와 request digest이면 `resolution_revision`이 만료됐더라도 기존 safe result를 반환하고
mutation을 반복하지 않는다. Manual KB의 source gate는 `not_applicable`이다. Source-managed KB의
denied/revoked/stale/unknown gate는 receipt lookup/result projection보다 먼저 `resource.hidden` safe `404`로 닫고
receipt, decision, satisfaction 또는 job identity를 노출하거나 변경하지 않는다. 같은 key의 다른 request는
conflict다. Receipt가 없을 때만 preview token scope/expiry와 current input을 검증한다.
Raw token과 내부 fingerprint는 저장하지 않으며 receipt는 적어도 연결된 job/result의 operational
retention과 같은 기본 30일 동안 유지한다. Receipt의 actor ref는 execution actor를 다시 찾기 위한
immutable provenance이지 authorization capability가 아니다. Source authorization이 외부 check를 요구하면
authorization session/transaction을 mutation 전에 종료하고 nullable bounded revision/watermark만 admission
transaction에 전달한다. Receipt replay return 및 fresh `unchanged/satisfied_existing/job_created/job_reused`
commit 직전에 current permission과 그 revision/watermark를 revoke와 직렬화되는 CAS 또는 동등한 authoritative
validation으로 다시 확인한다. Revoke가 winner이면 receipt replay를 반환하지 않고 fresh admission의 receipt,
manifest, satisfaction, current decision pointer, job과 `knowledge.processing_reindex.admitted` success audit를
모두 만들지 않는다. ADR-0017 resource-hidden 계약의 target/source 비노출 request-scoped security audit는
별도 경계이며 허용하되 receipt/result/job identity와 source 세부 정보를 포함하지 않는다.

Physical job을 최초 생성한 `job_created` receipt의 actor를 job의 immutable execution actor로 bind한다.
다른 authorized actor의 `job_reused` receipt는 기존 job actor를 교체하거나 공동 authorization capability가
되지 않는다. Original job actor가 authority를 잃으면 해당 job을 cancel하고, 다른 authorized actor가
terminal job 뒤 실행을 원하면 fresh preview와 새 idempotency request로 새 generation을 admission한다.

Physical embedding build가 필요한 fresh `job_created` admission은 LLM Credentials domain의 authoritative
Knowledge processing embedding binding port를 호출한다. Port는 resolved profile의 exact embedding model/provider와
job execution actor에 대해 같은 Organization의 active credential, verified model relation과 current `use`
permission을 평가한다. Eligible credential이 정확히 하나일 때만 server-owned binding을 발급하며 0개 또는
복수이면 preference, name/order, owner, default/preset 또는 다른 credential fallback 없이
`409 knowledge.processing_embedding_credential_unavailable`로 receipt, decision, job과 success audit 없이 닫는다.
Binding issue 뒤 fresh job commit 직전 credential lifecycle, relation, permission decision과 provider-routing
revision을 row lock/CAS 또는 동등한 serializable validation으로 다시 확인하고 revoke/change winner도 같은
fixed 409 zero-write로 닫는다. `job_reused` admission은 caller credential을 새로 선택하지 않고 기존 job의
execution actor와 binding을 그대로 반환한다. 그 binding의 current validity는 다음 claim/batch/finalization gate가
판정하며 invalid면 existing generation을 terminal cancel한다.

Binding은 job/attempt에 immutable하게 연결된 safe credential/model/provider reference, credential lifecycle
revision, verified relation revision, credential permission decision revision과 provider-routing revision만
보존한다. Credential value, encrypted config와 provider raw config는 profile, request/response, receipt, job
payload, audit와 trace에 넣지 않는다. Binding은 credential을 materialization profile이나 output identity로
승격하지 않으며, 새 generation은 현재 binding을 다시 발급받는다. 검토한 대안 중 profile에 credential ID를
저장하는 방식은 processing policy와 secret-resource lifecycle을 결합하므로 제외했고, first/latest/name/order
credential 자동 선택은 ADR-0064의 authoritative ownership과 revoke 검증을 우회하므로 제외했다. Exact-one
fail-closed binding은 별도 default/preset 정책 없이 모호한 선택을 만들지 않는 최소 계약이다.

Reindex worker는 claim과 각 source/provider external I/O batch 직전에 짧은 authorization session으로
job execution actor의 current active organization membership, effective KB `write` 또는 Organization manager
authority, applicable source authorization 및 bound embedding credential의 active lifecycle, exact
model/provider relation, `use` permission과 binding revision을 다시 평가한다. Authorization session/transaction은
외부 I/O 전에 종료한다. 어느 gate의 revoke/change가 먼저 commit되면 해당 batch의 adapter를 호출하지 않는다. Gate 뒤
commit된 revoke는 이미 시작한 bounded call을 강제 중단하지 않지만 다음 external batch와 finalization을
차단한다. Receipt, job ID, owner/fencing token과 admission 당시 permission 결과를 권한으로 재사용하지 않는다.

1. Current canonical content, assignment, registry/taxonomy, policy, override, profile catalog state와
   exact profile revision을 resolve하고 immutable Processing Decision Manifest를 만든다.
2. Current active Artifact Build Manifest의 materialization input fingerprint가 같고 integrity가 valid하면
   같은 짧은 CAS transaction에서 `satisfied_existing` Decision Satisfaction과 current decision pointer만
   확정한다. 새 DocumentVersion/index 또는 reindex job을 만들지 않는다.
3. Materialization fingerprint가 다르면 decision manifest와 위 identity에 묶인 durable job을 만들고
   create-only staging DocumentVersion/index와 derived representation을 준비한다.
4. Canonical lineage, chunk/token bound, embedding completeness, retrieval smoke와 output integrity hash를
   검증한다.
5. Finalizer는 execution actor의 current active organization membership, effective KB `write` 또는
   Organization manager authority와 bound embedding credential lifecycle, exact model/provider relation,
   `use` permission 및 binding revision을 다시 확인한다. Applicable source authorization은 finalization 직전
   fresh gate에서 평가하고 외부 호출/session을 종료한 뒤 bounded decision revision/watermark만 finalizer
   transaction에 전달한다. 같은 짧은 transaction에서 canonical content와 전체 resolver input vector를
   resolve해 두 input fingerprint를 재계산한다. Permission/source authorization revision 또는 watermark는
   revoke와 직렬화되는 authoritative revision/CAS 또는 동등한 serializable validation으로 pointer swap까지
   current임을 확인한다. Current
   pointer/revision row lock, conditional CAS 또는 동등한 serializable validation으로 비교 시점부터
   pointer swap까지 vector가 바뀌지 않게 한다. Admission snapshot과 하나라도 다르면 active pointer와
   satisfaction을 commit하지 않는다. Current job fencing/lease가 유효하면 input mismatch는 terminal
   `stale`로, membership/KB/source authorization loss는 terminal `cancelled`와 fixed
   `knowledge.processing_execution_not_authorized`, embedding binding loss는 terminal `cancelled`와 fixed
   `knowledge.processing_embedding_credential_unavailable` reason으로 닫고 해당 attempt가 소유한
   staging artifact의 idempotent cleanup outbox intent를 같은 transaction에서 확정한다. Lease/fence가
   유효하지 않으면 worker는 아무것도 commit하지 않고 reconciler가 orphan staging attempt를 수습한다.
6. Artifact가 ready이고 current authorization 및 input vector가 일치할 때만 immutable Artifact Build Manifest와
   `satisfied_new` Decision Satisfaction을 만들고 current decision pointer 및 active artifact pointer를
   원자적으로 교체한다.
7. Previous version과 stale/failed staging artifact cleanup은 durable outbox가 처리한다. Immutable
   Artifact Build Manifest와 Decision Satisfaction은 provenance reference이며 그 자체로 physical bytes를
   영구 pin하지 않는다. Handler는 cleanup generation/fencing, owning build attempt, current active artifact,
   current decision satisfaction, in-flight build, citation/evidence retention pin과 legal hold 부재를 다시
   확인한다. Explicit blocking pin이나 아직 만료되지 않은 retention이 있으면 삭제하지 않는다.
8. Eligible cleanup은 짧은 pre-delete transaction에서 artifact를 `purging`으로 fence하고 durable purge intent와
   impact snapshot revision을 확정한 뒤 physical object/vector/index를 idempotent하게 삭제한다. 이 transaction
   또는 intent 저장이 실패하면 external delete 전에 전체 rollback한다. `purging` artifact는 retrieval, citation과
   `satisfied_existing` 재사용 대상이 아니다. Physical deletion을 확인한 뒤 별도 completion transaction에서
   append-only Artifact Availability Tombstone, `purged` availability, impact snapshot revision과 canonical
   `knowledge.processing_artifact.purged` audit를 함께 확정한다. Audit/tombstone commit이 실패해도 이미 삭제된
   artifact를 `ready`로 되돌리지 않고 non-retrievable `purging`으로 유지한다. Reconciler는 storage absence와
   같은 generation을 확인해 completion transaction을 idempotent하게 재시도하며 exact replay는 tombstone과
   audit를 중복 생성하지 않는다. Outbox 실패는 retry하고 reconciler는 terminal attempt에 cleanup intent가
   없거나 미완료인 상태를 복구한다.

실패, timeout, stale revision과 rollback은 staging/failed artifact만 남기고 기존 active ready
version을 retrieval-visible하게 유지한다. 정책 publish나 PR merge만으로 기존 KB 전체 reindex를
자동 시작하지 않는다.

### 8. Canonical evidence와 retrieval representation을 분리한다

Redacted canonical evidence는 citation과 lineage의 source다. Contextual prefix, parent summary,
late/visual embedding input 등 검색용 derived representation은 canonical content를 덮어쓰지 않고
별도 identity, version, fingerprint와 Artifact Build Manifest reference를 가진다. Query intent는
document type과 분리된 request-scoped 개념이며 document type 하나가 모든 질의의 retrieval
strategy를 고정하지 않는다.

RAPTOR-like tree, long retrieval unit, graph/global retrieval, late chunking, visual retrieval와
reranking은 문서·질의 track별 evidence가 있을 때만 opt-in 후보로 도입한다. 하나의 advanced RAG
profile로 묶거나 전역 기본값으로 승인하지 않는다.

### 9. Suggestion과 감사 데이터는 최소화한다

Durable suggestion에는 stable candidate ID, bounded state/outcome, exact version refs, classifier
contract refs, calibrated 여부와 safe reason만 저장한다. Raw document excerpt, prompt,
completion, chain-of-thought/rationale, provider raw response와 credential을 저장하지 않는다.
Accepted/rejected outcome을 model training에 2차 이용하려면 별도 organization policy와 승인이
필요하다.

Suggestion의 review 가능 기간은 생성 시점부터 최대 30일이다. Content/taxonomy/registry 변경은
그보다 먼저 expire시킬 수 있다. Terminal 또는 expired suggestion은 전이 시점부터 최대 30일 뒤
purge하며 canonical assignment revision과 audit retention은 이 operational row와 분리한다. Legal
hold 또는 장기 품질 학습을 이유로 raw suggestion payload를 연장 보존하지 않는다.

Accepted suggestion purge 전 accept transaction은 위 immutable safe provenance snapshot을 assignment
revision과 canonical audit에 함께 고정한다. Purge는 operational suggestion row만 제거하며 safe contract
refs와 accepted outcome은 assignment lifecycle 동안 재현 가능해야 한다.

Authoritative mutation과 successful current-validation audit에는 organization/KB, actor type과 safe
actor ref, before/after stable ID와 axis별 authority source/lock, last-validated safe refs,
taxonomy/profile/assignment/draft revision, fixed safe reason과 correlation을 남긴다. Raw label 설명, source title/path/URL과
document content는 audit metadata에 넣지 않는다.
Suggestion accept audit에는 assignment에 snapshot한 `generator_kind`와 safe `generator_contract_ref`를 남긴다.
AI kind에서만 model/prompt-template/calibration safe refs와 bounded confidence bucket을 추가하며 deterministic
kind에 fake provider provenance를 만들지 않는다. Raw score, prompt, rationale와 content는 저장하지 않는다.

이 ADR이 문자열까지 확정한 canonical action은 다음 22개다.

- Taxonomy: `knowledge.taxonomy_draft.created`, `knowledge.taxonomy_draft.updated`, `knowledge.taxonomy.published`
- Classification: `knowledge.classification_assignment.created`, `knowledge.classification_assignment.updated`,
  `knowledge.classification_assignment.locked`, `knowledge.classification_assignment.unlocked`,
  `knowledge.classification.revalidated`, `knowledge.classification_suggestion.accepted`,
  `knowledge.classification_suggestion.rejected`
- Processing Profile: `knowledge.processing_profile_draft.created`, `knowledge.processing_profile_draft.updated`,
  `knowledge.processing_profile.published`, `knowledge.processing_profile.deprecated`
- Profile Policy: `knowledge.processing_profile_policy_draft.created`,
  `knowledge.processing_profile_policy_draft.updated`, `knowledge.processing_profile_policy.published`
- Profile control/runtime: `knowledge.processing_profile_default.changed`,
  `knowledge.processing_profile_override.set`, `knowledge.processing_profile_override.clear`,
  `knowledge.processing_reindex.admitted`
- Artifact lifecycle: `knowledge.processing_artifact.purged`

모두 ADR-0008 canonical table, `AuditAction`, audit 검색/UI filter와 계약 테스트에 같은 문자열로 등록하고
mutation과 같은 Unit of Work에서 exactly-once 또는 terminal replay 계약을 적용한다.

Taxonomy publish audit은 impact preview token 원문 대신 safe token digest, acknowledged flag, bounded impact
bucket과 impact snapshot revision만 기록한다. Organization profile publish/deprecate audit은 stable profile/ref,
before/after lifecycle state와 catalog revision만 기록한다. Platform default change audit은 system actor, safe
before/after Platform profile revision과 resulting catalog revision만 기록한다. 두 audit 모두 raw profile config를
포함하지 않는다. Profile-policy publish audit도 token 원문 대신 safe token digest, acknowledged flag,
`profile_policy_impact_bucket_v1`, bounded affected/reindex bucket과 assignment impact snapshot revision만 기록하고
raw policy/profile config와 exact count를 포함하지 않는다.
Artifact purge success는 physical deletion이 확인되고 Artifact Availability Tombstone이 commit되는 completion
transaction에서만 canonical `knowledge.processing_artifact.purged`로 기록한다. Opaque manifest/artifact ref,
purge generation, fixed reason, pin evaluation outcome과 tombstone ref만 저장하고 storage locator와 content를
기록하지 않는다. Pre-delete `purging` fence/intent는 이 success action을 만들지 않으며 completion audit 실패 뒤
reconciler의 exact-generation retry도 action을 중복 생성하지 않는다.
Reindex 최초 successful admission은 public result 네 종류 모두 canonical
`knowledge.processing_reindex.admitted`, `audit_logs.status='success'`를 사용하고 safe metadata의
`result_status=unchanged|satisfied_existing|job_created|job_reused`로 구분한다. Receipt와 각 result에 필요한
decision/satisfaction/job/current-pointer mutation 및 audit은 한 Unit of Work에서 commit 또는 rollback한다.
Audit 실패는 receipt를 포함한 admission 전체를 rollback한다. Exact authorized receipt replay는 read-only
recovery이므로 새 audit를 만들지 않고 최초 audit를 재사용하며, idempotency conflict와 source-hidden 실패는 이
success action을 만들지 않는다. Embedding credential unavailable도 success action을 만들지 않는다. Metadata는
organization/KB/safe actor, opaque receipt/result/job ref와 safe
revision만 허용하고 idempotency key/token digest, 내부 fingerprint, source identity, credential identity/candidate
count와 revoked grant/relation/permission detail을 포함하지 않는다.
Reindex execution authorization loss의 terminal event는 organization/KB, safe execution actor ref,
opaque job/attempt ref, bounded stage와 fixed `knowledge.processing_execution_not_authorized` 또는
`knowledge.processing_embedding_credential_unavailable` reason만 기록한다. 어떤 membership/grant/source ACL,
credential lifecycle/relation/permission이 회수됐는지, source/credential identity, content와 provider payload는
기록하지 않는다.

### 10. Production default는 confirmatory evidence 뒤에만 승인한다

MBA-279 결과는 exploratory development evidence다. 문서 유형별 production profile,
hierarchical/contextual representation 또는 candidate diversification을 기본값으로 바꾸기 전에
MBA-309에서 human-reviewed multi-document-type holdout을 사용해 다음을 사전 등록한다.

- Primary metric과 동일 context-token budget
- 최소 제품 효과 또는 non-inferiority margin
- Classifier 최대 risk와 minimum coverage
- Sampling/cluster/power 근거
- Resampling, multiplicity와 stopping rule
- Latency, cost, citation correctness와 rollback gate

검증되지 않은 후보는 disabled, shadow 또는 explicit opt-in 상태를 유지한다.

## Rationale

- 서로 다른 권위와 실패 영향을 가진 분류 축을 분리해 검색 품질 metadata가 보안·권한을
  변경하지 못하게 한다.
- Stable ID와 immutable version으로 rename, move, deprecate 뒤에도 과거 판단을 재현한다.
- Versioned server label normalizer와 append-only replacement edge ledger로 sibling uniqueness와 graph depth를
  구현 시점이나 historical version 정리 여부에 따라 다르게 판정하지 않는다.
- Complete taxonomy topic 1,000개/candidate edge 2,000개, assignment topic 32개와 replacement path 8 edge의
  V1 bound로 authoring, request, projection, audit와 graph traversal을 제한하되 retrieval result 수나 chunk
  recall을 인위적으로 제한하지 않는다.
- Manual lock, suggestion-only AI와 calibrated confidence 경계로 자동 분류 오류의 영향을
  제한한다.
- Immutable profile, Processing Decision Manifest와 Artifact Build Manifest로 정책 판단과 production
  artifact를 각각 재현한다.
- Create-only activation과 기존 active-ready 보존으로 단계적 도입 중 RAG availability를
  유지한다.
- Override의 nullable assignment/profile-policy precondition과 opaque full resolver revision을 함께 요구해
  Client가 관찰한 pointer와 server가 직렬화한 전체 vector가 다른 상태로 mutation되는 것을 막는다.
- Impact preview acknowledgement를 exact draft, policy/catalog와 assignment impact snapshot에 bind해 UI를
  우회한 organization-wide taxonomy publish를 차단한다.
- Content freshness를 axis별 authority와 분리해 explicit lock은 보존하되 stale unlocked classification이 새
  content의 profile/filter/materialization에 사용되지 않게 한다.
- Organization profile의 authoring lifecycle을 명시해 policy/override가 실제로 만들 수 있는 published
  revision만 참조하게 한다.
- Manifest provenance와 physical retention pin을 분리하고 purge tombstone을 남겨 storage 회수와 lineage
  재현을 동시에 유지한다.

## Consequences

- MBA-305 자체는 문서 정책만 승인하며 table, migration, endpoint, Client 또는 classifier를
  구현하지 않는다.
- [Knowledge 보호 리소스 완결성 매트릭스](../features/knowledge/protected-resource-completion.md)는
  정책·공식 문서 경계만 완료로 기록한다. 동작 경계는 MBA-335, MBA-304, MBA-310, MBA-311과
  MBA-309의 구현·검증 증거가 추가되기 전까지 완료로 간주하지 않는다.
- 현재 `meta_info.classification`은 security compatibility source로 계속 동작한다. Legacy의
  비표준 값은 inventory와 migration 전까지 document type/topic으로 자동 변환하지 않는다.
- Taxonomy/manual assignment, token-based profile, contextual representation, diversification와
  confirmatory benchmark는 각각 별도 이슈로 구현한다.
- Target logical entity와 API 이름은 feature/data-model 문서에 기록하지만 physical schema와
  rollout은 해당 구현 PR에서 additive migration으로 확정한다.
- 조직별 taxonomy, profile catalog와 profile policy 운영에는 별도 관리자 UI와 audit/review workflow가 필요하다.

## Affected Files

- `docs/decisions/README.md`
- `docs/glossary.md`
- `docs/data_model.md`
- `docs/architecture.md`
- `docs/features/knowledge/requirements.md`
- `docs/features/knowledge/api_spec.md`
- `docs/features/knowledge/component_spec.md`
- `docs/features/knowledge/test_cases.md`
- `docs/features/knowledge/protected-resource-completion.md`

## Follow-up Review Notes

- MBA-335는 taxonomy/manual classification과 Processing Profile control-plane의 physical schema,
  API/UI, permission, lifecycle, audit와 concurrency를 구현한다. Physical schema의 unique/index/FK,
  retention과 migration inventory를 확정해야 한다. Server normalizer contract와 Unicode data version,
  sibling comparison key index, server-issued topic ID/current-tree bounded draft-key recovery mapping, complete
  taxonomy topic 1,000개/replacement edge 2,000개 bound, assignment topic `maxItems=32`, depth-8 graph validator와
  `taxonomy_impact_bucket_v1` predicate/boundary를 같은 구현 매트릭스에서 검증한다.
- MBA-304는 immutable token-based profile, decision/build/satisfaction records, legacy adapter와
  create-only reindex activation을 구현한다. Receipt lookup 전 applicable source gate, return/commit 직전
  revision validation과 네 result의 `knowledge.processing_reindex.admitted` atomic audit/exact replay
  deduplication을 포함한다.
- MBA-310은 canonical evidence와 deterministic contextual retrieval representation의 physical
  storage/fingerprint 경계를 구현한다.
- MBA-311은 current/history duplicate candidate diversification을 독립 factor로 구현한다.
- MBA-309는 production mapping/default를 승인하기 전 confirmatory benchmark를 수행한다.
- AI classifier issue는 published taxonomy, manual review UI, labeled calibration/test split,
  provider egress gate와 abstention policy가 준비된 뒤에만 생성한다.
