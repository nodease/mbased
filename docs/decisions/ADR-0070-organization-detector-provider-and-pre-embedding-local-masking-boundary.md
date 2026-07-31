# ADR-0070: 조직 Detector Provider와 embedding 전 로컬 마스킹 경계

Status: Accepted

## Related Decisions

- [ADR-0008](ADR-0008-audit-action-naming-standard.md)
- [ADR-0010](ADR-0010-resource-access-403-404-policy.md)
- [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md)
- [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md)
- [ADR-0052](ADR-0052-knowledge-document-ingestion-durable-execution-boundary.md)
- [ADR-0057](ADR-0057-llm-credential-at-rest-encryption-and-rotation.md)
- [ADR-0064](ADR-0064-provider-execution-capability-boundary.md)
- [ADR-0065](ADR-0065-knowledge-classification-taxonomy-and-processing-profile.md)
- [ADR-0067](ADR-0067-production-https-and-operation-bound-outbound.md)

## Context

ADR-0014는 chunk content, embedding input과 retrieval-visible text를 redacted
canonical text에서 생성하고 platform hard baseline을 조직 정책이 약화하지 못하게
한다. ADR-0065는 redacted canonical bytes와 canonicalization contract를 독립된
Canonical Content Revision의 입력으로 두고 Processing Decision/Artifact Build와
분리한다.

현재 `apps/gateway/services/ingestion/service.py`의 `process_document()`와
`process_document_for_job()`은 `_extract_raw_blocks()` 뒤
`_build_document_chunks()`와 `_persist_chunks_with_embeddings()`를 호출한다.
추출 content를 authoritative hard-baseline detector, organization detector와 local
masking finalizer가 강제하는 단계는 없다. 현재 `TraceRedactionService`는 trace
payload 저장용 정책 경계이며 Knowledge canonicalization, provider 선택, document
finalization의 권위가 아니다. 따라서 기존 chunk/vector를 이 ADR을 통과한 artifact로
간주할 수 없다.

조직별 DLP/NER provider를 단순히 이 경로에 추가하면 다음 위험이 생긴다.

- baseline 검사 전 원문이 외부 detector 또는 embedding provider로 전송된다.
- provider timeout을 baseline-only로 낮춰 조직이 요구한 보호 수준을 우회한다.
- provider별 UTF-16/code-point/token offset 차이로 다른 문자를 마스킹한다.
- 다른 Organization의 provider revision이나 credential을 fallback으로 선택한다.
- 일부 segment만 성공한 결과로 chunk/embedding을 만들고 active version을 전환한다.
- raw text, exact span 또는 provider response가 job/audit/trace/log에 남는다.
- 검사 중 actor 권한, source authorization, policy 또는 provider가 회수되어도 stale
  worker가 결과를 finalize한다.

## Options Considered

1. Trace redaction service를 ingestion 직전에 그대로 호출한다.
   - 공통 pattern 일부는 재사용할 수 있지만 trace storage policy와 Knowledge artifact
     lifecycle, provider egress와 finalization 책임이 섞인다.
2. Organization detector가 raw text와 redacted output을 모두 만들게 한다.
   - Provider 호출 자체가 원문 egress가 되고 provider별 결과가 canonical source가 된다.
3. 로컬 hard baseline, 명시적 organization provider, provider-neutral span contract와
   local deterministic masking을 순서대로 적용한다.
   - 경계가 늘어나지만 platform minimum, organization policy, 재현성과 fail-closed
     finalization을 함께 보존한다.

3번을 채택한다.

## Decision

### 1. Current와 Target을 구분한다

이 ADR은 Target policy authority다. MBA-362가 runtime, persistence, provider adapter와
테스트를 구현하기 전까지 다음 상태를 유지한다.

- Current ingestion과 기존 artifact는 privacy gate 준수를 주장하지 않는다.
- Current `documents.file_path`와
  `GET /api/v1/knowledge/{kb_id}/documents/{document_id}/content`가 보존·반환하는 upload 원본은
  legacy current behavior다. 이 route는 Target dedicated raw/compliance surface가 아니며 이를
  opt-in protected raw artifact 또는 Target privacy gate 준수 증거로 해석하지 않는다.
- Target enforcement cutover는 Nodease가 보존한 upload/fetch 원문 copy를 먼저 exact inventory로
  동결하고 각 item을 `protected_migrated|purged` 중 하나의 terminal disposition으로 수렴시켜야 한다.
  Organization/source opt-in이 유효하고 적용 가능한 retention/legal hold가 보존을 허용하거나 요구하는 원문은 encrypted protected raw artifact로
  이관하고 기존 storage copy의 physical absence를 확인한다. Protected migration 조건을 충족하지 않고
  legal hold가 삭제를 막지 않는 원문은 먼저 non-readable로 fence한 뒤 물리 삭제하고 durable purge
  receipt를 확정한다. Opt-in 없이
  legal hold가 삭제를 막거나 disposition이 불명확한 원문은 자동 이관하지 않고 cutover를 차단한다. Unknown/partial disposition 또는 기존
  storage copy가 하나라도 남으면 enforcement를 활성화하지 않는다.
- Raw body response 차단은 위 storage disposition의 대안이 아니다. Dedicated raw permission, fresh source
  ACL, pre-response access audit와 retention/legal-hold/purge gate가 모두 준비되고 해당 item이
  `protected_migrated`인 경우에만 dedicated raw/compliance surface에서 반환한다. 그 전에는 current
  content route를 fail-closed한다. Raw-derived retrieval artifact의 한시적 전환은 별도 frozen legacy wave
  계약만 따르며 비보호 원문 copy 보존을 정당화하지 않는다.
- Organization detector provider path는 비활성이다.
- Current `llamaparse` strategy는 baseline 전에 원본 bytes를 외부 processor로 전송할 수 있으므로
  Target protected raw parser egress 준수 증거가 아니다. Target enforcement에서는 아래 raw parser
  approval/operation-profile/transport readiness가 닫히기 전 `llamaparse`를 포함한 모든 external
  parser strategy를 비활성화하고 upload 전에 fail-closed한다.
- Provider lifecycle/egress-approval 경계가 없으면 non-null provider path를, canonical review
  management/audit 경계가 없으면 review-capable mode/action을 활성화하지 않는다.
- Policy 문서 병합만으로 기존 문서를 compliant로 표시하거나 자동 reindex하지 않는다.

Target 순서는 다음과 같다.

```text
actor/organization/KB/source authorization
  -> effective scoped policy/provider/egress approval and platform/Organization validity epoch snapshot
  -> content safety gate
  -> local isolated parsing (default)
     OR protected raw parser egress (explicitly approved exception)
  -> ephemeral normalized raw text
  -> local built-in hard-baseline detector
  -> exact organization detector provider when policy requires it
  -> validated union of baseline/provider spans
  -> local deterministic masking
  -> staged redacted candidate
  -> exact-candidate manual review/more masking when policy requires it
  -> approved redacted canonical content revision
  -> chunk/derived representation
  -> embedding/index
  -> manifest with platform/Organization validity epochs
  -> ready validation and atomic active pointer finalization
  -> retrieval prefilter and final evidence validity gates
```

앞 단계가 실패하거나 unknown이면 뒤 단계 external I/O와 retrieval-visible write를
수행하지 않는다. Content safety와 privacy detection은 서로 대체하지 않는다. Hard baseline은
parsed text가 있어야 실행할 수 있으므로 "baseline always first"는 local parsing 뒤 Organization
detector와 embedding보다 먼저라는 뜻이다. Protected raw parser egress는 이 순서의 유일한 명시적
예외이며 별도 approval과 transport가 닫히지 않으면 원본 bytes를 외부로 보내지 않는다.
Hard baseline action policy가 terminal `block`을 결정하면 Organization provider를 호출하지
않고 `knowledge.sensitive_content_detected`로 종료한다. Provider 결과에서 terminal `block`이
결정되면 embedding/finalization을 수행하지 않는다.

### 2. 책임 경계를 분리한다

| 경계 | 책임 | 금지 |
| --- | --- | --- |
| Shared Privacy Core | hard baseline, normalization helper, span 검증/변환/union, deterministic masking | DB, HTTP, FastAPI, provider SDK와 Knowledge lifecycle import |
| Effective Privacy Policy Resolver | platform hard baseline, Organization base, explicitly bound Collection과 applicable source/KB stricter revision을 보수적으로 합성하고 exact scope/binding snapshot과 digest를 만든다 | routing membership의 privacy authority 사용, mutable latest/순서 기반 override, 하위 scope의 약화, client-supplied scope |
| Knowledge Ingestion Application | actor/source gate, effective policy snapshot, raw handle 수명, provider 조율, canonicalization/finalization | request/graph에서 provider/credential 선택, raw를 job payload로 직렬화 |
| Protected Raw Parser Egress Port/Adapter | 승인된 external parser에 원본 bytes를 제한 전송하고 parsed output을 local privacy pipeline으로 되돌린다 | 일반 detector approval 재사용, readiness 전 external parser 호출, parser output의 baseline 우회 |
| Detector Provider Port/Adapter | provider별 wire/offset/error를 내부 contract로 변환 | policy 선택, baseline span 삭제, active pointer 변경 |
| Outbound Egress Guard | endpoint/network/credential capability/timeout/size/concurrency 정책 | detector 의미와 document 권한 판단 |
| Audit/Tracing | safe operational outcome/correlation 저장 | Knowledge canonicalization 또는 원문/span 저장 |

Audit/Tracing과 Knowledge는 순수 detector/masking primitive를 공유할 수 있지만
`TraceRedactionService` 자체를 Knowledge application service로 호출하지 않는다.

### 3. Actor, command/query와 precondition

| 작업 | Actor/scope | Precondition과 재검증 |
| --- | --- | --- |
| process/reprocess | active Organization 사용자, KB `write` 또는 Organization manager | active KB/document, applicable source authorization, current effective privacy policy와 parser path readiness |
| sync/reindex worker | admission에 불변 bind된 execution actor | claim/각 external batch/finalize 전 active membership, KB authority, source/effective-policy/parser/provider/approval/validity revision 재검증 |
| migration wave provision | deployment-owned platform migration principal, explicit Organization rollout allowlist | enforcement 활성화 전 exact current legacy artifact set, prior wave/receipt 없음, valid deadline/cutoff, atomic canonical audit |
| migration reindex | 명시적으로 승인된 organization-scoped operator path | ambient system bypass 금지, 동일 privacy/finalization gate |
| private detector operation-profile rollout | Organization RBAC 밖의 deployment/change-control operator | server-owned registry의 exact host/port/CIDR와 dedicated worker/network policy revision을 변경 검토로 확정하고 runtime/API mutation을 제공하지 않음 |
| review list/detail | active Organization의 Organization manager | source-managed document는 projection 직전 fresh requester source authorization/display policy, manual KB는 source gate 비적용 |
| review mask/approve/reject | active Organization의 Organization manager | exact pending generation/candidate revision, source-managed document는 mutation commit 직전 fresh requester source authorization/display policy 재검증, raw access와 분리 |
| Collection privacy bind/unbind | active Organization manager | current same-Organization active routing membership, exact published Collection policy revision, impact preview token/acknowledgement, expected membership/binding/policy/Organization validity revision |
| deployment preflight | 기존 deployment command actor와 server-derived audience | runtime과 같은 retrieval-visible resolver로 current-valid manifest, active-pointer legacy retirement와 cutoff를 평가 |
| status/query | 기존 document/job read authority | safe projection, ownership-first resource hiding, current privacy artifact validity |

Background execution actor의 membership/권한이 회수되면 다른 사용자, document owner,
deployment owner 또는 ambient system actor로 승격하지 않는다. Source-managed resource는
Organization manager도 fresh requester source authorization/display/raw policy를 우회하지
않는다. External authorization session과 DB transaction은 network I/O 전에 닫고 bounded
revision/watermark만 commit-time validation에 전달한다.

Deployment-owned migration principal은 public User를 가장하지 않는 typed system actor이며
deployment configuration의 change-controlled explicit Organization allowlist에만 작동한다.
Allowlist/wave가 없는 Organization의 기본값은 legacy grace가 아니라 retrieval-unavailable이다.
Ambient system actor, document owner 또는 Organization membership만으로 wave를 만들 수 없다.

`ready`/active pointer는 build 및 storage 상태이고 현재 privacy 준수 권한이 아니다. V1의
server-owned Privacy Artifact Validity Vector는 global platform revision/monotonic epoch와
same-Organization revision/monotonic epoch의 정확한 두 요소다. Manifest는 finalization 때 두
revision ref와 epoch를 snapshot하고 Retrieval은 prefilter와 final evidence gate에서 current 두
epoch와 모두 일치하는지 평가한다. Manifest, current revision 또는 어느 epoch든 missing/malformed/
stale하면 기존 pointer/cache/vector hit와 무관하게 fail-closed한다. Client는 scope, revision 또는
epoch를 입력하거나 완화할 수 없다.

### 4. Policy mode와 provider 선택

| Mode | Provider revision | Detector | Finalization |
| --- | --- | --- | --- |
| `baseline_only` | 반드시 `NULL` | local hard baseline만 실행 | `block`은 차단, `manual_review`는 review pending, 그 외 action policy가 허용하면 자동 |
| `enterprise_detector_required` | exactly one same-Organization active revision 필수 | baseline 뒤 exact provider 실행 | `block`은 차단, `manual_review`는 review pending, 나머지는 provider result까지 유효해야 자동 |
| `manual_review_required` | `NULL` 또는 exactly one same-Organization active revision | baseline을 실행하고 provider ref가 있으면 exact provider도 실행 | terminal `block`이 아니면 action과 무관하게 항상 review pending |

- `optional provider failure -> baseline-only` mode는 두지 않는다.
- Platform baseline ruleset/action은 Organization이 끄거나 약화할 수 없다.
- Provider result는 baseline에 additive하며 span/action을 제거하지 못한다.
- Effective policy snapshot은 platform/Organization/Collection/source/KB exact policy와 binding
  revision, compiled digest, baseline ruleset, action policy, normalization/masking contract와 nullable
  exact provider revision을 고정한다.
- Mode/provider cardinality는 policy publish/activate와 runtime snapshot에서 같은 closed matrix로
  검증한다. `baseline_only`의 non-null provider, `enterprise_detector_required`의 null/복수 provider,
  cross-Organization 또는 inactive revision은 invalid policy이며 provider/embedding 전에 닫는다.
- `manual_review_required`는 provider를 참조하지 않는 baseline-only review와 exact provider를
  포함하는 review를 모두 허용한다. Provider credential/capability readiness는 non-null provider
  ref일 때만 요구한다.
- Effective action policy가 결과로 `manual_review`를 만들 수 있거나 mode가
  `manual_review_required`이면 canonical review management/audit readiness가 policy 활성화의
  필수 precondition이다. Readiness가 없으면 action을 `mask`로 낮추지 않고 policy 활성화를
  거부한다.
- `first`, mutable `latest`, name/order/owner/default, 다른 Organization과 environment
  fallback을 금지한다.
- Request, graph, source/document metadata에서 provider id, endpoint, config 또는
  credential을 받지 않는다.

Effective privacy policy는 다음 scope를 하나의 보수적 결과로 합성한다.

1. Platform hard baseline은 항상 적용하며 어떤 하위 scope도 끄거나 약화할 수 없다.
2. Current Organization base revision은 필수다.
3. KB에 대해 current active인 모든 explicit Collection Privacy Policy Binding이 가리키는 applicable
   stricter Collection revision, applicable source revision과 KB revision을 모두 포함한다. Collection
   UUID 정렬은 snapshot 재현성에만 사용하며 우선순위 또는 last-writer-wins 의미가 아니다.
4. Category별 action은 `block > manual_review > mask` 중 가장 강한 값을 사용하고,
   detector-required와 manual-review-required 조건은 OR로 합성한다. Distinct non-null provider
   revision 집합은 0개 또는 정확히 1개여야 한다. 복수 provider, required detector의 null provider,
   cross-Organization/inactive revision과 서로 양립할 수 없는 조합은 policy unavailable로
   fail-closed한다.
5. Resolver는 platform/Organization/정렬된 Collection/source/KB exact revision ref, Collection
   Privacy Policy Binding과 source/KB binding revision, compiled effective-policy digest, derived mode/action/provider를
   snapshot한다. Finalization은 같은 scope set과 binding을 다시 resolve한다.

상세 scope는 base보다 강하게만 만들 수 있고 제거/미지정은 상위 값을 유지한다. Applicable
Collection Privacy Policy Binding, source binding 또는 scoped policy 변경이 effective result나 적용 scope set을
바꾸면 V1 compatibility validator는 이를 `artifact_invalidating`으로 분류해 해당 Organization
validity epoch을 정확히 1 증가시킨다. 따라서 V1 manifest validity vector는 platform/Organization 두
요소를 유지하면서도 세부 scope 변경을 빠뜨리지 않는다.

Collection의 일반 item membership은 검색 grouping/routing 리소스이며 privacy policy binding이 아니다.
`catalog_manage` 또는 `collection.manage`만으로 item을 link/reorder해도 Collection privacy policy는 KB에
적용되지 않는다. Privacy binding은 별도 immutable protected revision이며 V1에서는 Organization manager만
impact preview를 확인하고 exact expected revisions를 제출해 생성/교체/제거한다. Binding은 current active
same-Organization membership이 있을 때만 만들 수 있다. Active privacy binding이 남아 있으면 일반 membership
unlink를 `knowledge_collection_privacy_binding_active`로 zero-write 차단하고 Organization manager가 먼저
privacy binding을 별도 impact flow로 제거해야 한다. 이로써 catalog 관리자가 접근할 수 없는 KB의 privacy
정책 또는 Organization-wide validity epoch을 membership mutation만으로 바꾸지 못한다.
Collection archive/restore도 routing lifecycle일 뿐 active privacy binding을 활성화하거나 비활성화하지
않는다. Archive 뒤에도 binding과 referenced published policy는 Organization manager가 explicit unbind할 때까지
effective scope에 남고 Organization validity epoch은 lifecycle mutation만으로 바뀌지 않는다. Hard delete 또는
policy revision purge는 active binding을 cascade 삭제하지 않고 explicit binding retirement/impact flow 전에는
차단한다. 따라서 `lifecycle_manage`도 privacy authority가 아니다.

`manual_review_required`의 pending review projection은 staged redacted candidate,
bounded safe outcome과 opaque manifest/generation ref만 사용한다. Exact span, provider view/map,
raw content 또는 reversible raw-to-redacted mapping을 review 편의를 위해 durable 저장하지 않는다.
Reviewer가 원문을 확인해야 하면 ADR-0014의 별도 raw/compliance permission, fresh source ACL,
access audit와 retention/legal-hold/purge를 통과한 전용 raw surface를 사용하며 review authority
자체는 raw access를 부여하지 않는다.

Provider가 `NULL`인 `manual_review_required`는 지원하는 정식 비LLM 경로다. Organization manager는
staged redacted candidate의 exact generation/revision을 승인하거나, candidate UTF-8 byte range에 대한
추가 mask operation만 제출할 수 있다. Replacement text나 전체 content를 command body로 받지 않으며,
서버는 range를 검증해 기존 candidate에서 정보가 줄어드는 새 immutable candidate revision을 deterministic
masking으로 만든다. 새 revision은 local hard baseline을 다시 통과하고 pending 상태를 유지하며, 별도 exact
revision approval이 성공해야 canonical content와 embedding/finalization으로 진행한다.

Review command는 candidate row를 덮어쓰지 않는다. Exact generation/candidate revision, actor, action,
current policy/binding/validity/source precondition, safe outcome과 optional successor candidate ref를 결속한
append-only Privacy Review Decision을 생성한다. Mask 성공은 successor candidate revision, 그 revision의
manifest와 decision을 같은 Unit of Work에 확정하고 range 원문은 저장하지 않는다. Approve/reject는 exact
current candidate에 대한 decision과 generation review-state 전이를 canonical review audit와 원자 확정한다.
Embedding은 approval transaction 밖에서 approved immutable candidate/manifest로 재개하고 finalizer가 같은
authority/policy/validity/fence를 다시 검증한다.

사전 승인은 보지 않은 미래 원문이나 KB 전체에 대한 blanket bypass가 아니다. Finalization 전 생성된 exact
candidate digest/generation, effective policy/binding, platform/Organization validity와 source authorization에
결속된 approval만 허용한다. Content, candidate, policy/binding, validity 또는 source revision이 바뀌면 승인도
stale해져 fresh review가 필요하다. 사후 검토는 quarantine/pending candidate에 같은 command를 적용한다.
Platform hard baseline 또는 effective action의 terminal `block`은 reviewer가 approve/mask로 낮출 수 없으며
LLM 미사용, 수동 masking 또는 manager 지위가 이 불변조건을 우회하지 않는다.

이 문서에서 사전 승인은 retrieval-visible finalization 전 exact candidate 승인이고, 사후 검토는 detector 또는
ingestion이 pending/quarantine으로 만든 candidate의 remediation이다. 둘 다 최초 KB 사용 전에 완료되어야 하며,
이미 raw 또는 미승인 artifact를 검색에 노출한 뒤 소급 승인하는 post-use ratification은 허용하지 않는다.
업로드 전 별도 업무 승인은 provenance가 될 수 있지만 exact privacy candidate approval을 대체하지 않는다.

Source-managed pending candidate도 source-derived content다. Review list/detail projection 직전과
mask/approve/reject mutation commit 직전에 requester의 fresh source authorization과 applicable display
policy를 각각 재검증한다. Review role 또는 Organization manager 지위는 이 gate를 우회하지 않는다.
Revoke가 이기면 ownership-first `404 resource.hidden`으로 닫고 candidate/ref/state를 반환하거나
mutation/audit를 남기지 않는다. Manual KB에는 source gate를 합성하지 않는다.

Provider trust tier는 다음 둘이다.

| Tier | 허용 입력 |
| --- | --- |
| `organization_private` | Organization이 승인한 private processing boundary의 endpoint에만 bounded normalized segment |
| `external_approved` | baseline span을 로컬에서 제거/치환한 provider-safe view만 |

Built-in baseline은 provider resource가 아니고 trust tier를 갖지 않는다.

모든 provider revision은 exact same-Organization immutable Detector Egress Approval Revision을
참조한다. Approval은 provider/trust tier, purpose, processor/ownership boundary, endpoint/network
boundary, data residency, retention, no-training/no-secondary-use와 active/expiry/revoke 상태를
고정한다. `organization_private`는 이 중 private boundary 승인이 current일 때만 exact normalized
segment를 받을 수 있다. Private IP, DNS zone, mTLS 또는 dedicated credential만으로 승인된
processing boundary가 되지 않는다. Missing/stale/expired/revoked approval은 adapter 호출 0회이고,
각 external batch와 finalization 전 exact approval revision을 다시 검증한다.

### 5. Provider-neutral text와 span contract

초기 privacy text contract는 `privacy_text_unicode_14_0_nfc_lf_v1`이다.

1. Parser가 허용한 Unicode text의 CRLF/CR을 LF로 바꾼다.
2. Unicode Character Database 14.0.0의 NFC를 적용한다.
3. 다른 whitespace, paragraph boundary와 case를 보존한다.
4. Exact normalized UTF-8 bytes에 attempt-local byte-binding fingerprint를 계산한다.
5. Deterministic bounded segment와 overlap을 만들고 각 exact segment/view에 별도
   fingerprint를 계산한다.

ADR-0065의 taxonomy label comparison contract
`unicode_14_0_nfkc_casefold_ws_v1`은 NFKC/case-fold/whitespace 축약을 수행하는 별도
계약이다. Privacy text에 재사용하지 않는다. Unicode data version 또는 normalization
contract 변경은 새 canonicalization contract와 reindex를 요구한다.

내부 canonical span은 normalized document UTF-8 bytes 기준 half-open range
`[start_byte, end_byte)`다. Range는 non-empty/in-bound이고 양 끝이 UTF-8 code point
boundary여야 한다. Provider의 UTF-16 code unit, Unicode scalar 또는 code-point offset은
adapter가 이 좌표로 변환한 뒤 반환한다. Token offset만 있는 응답은 V1에서 지원하지
않는다. 별도 adapter가 exact transmitted character boundary와 versioned tokenizer mapping을
함께 검증할 수 있을 때만 provider contract extension으로 승인하며, 그렇지 않으면 readiness
또는 전체 response를 fail-closed한다.

각 adapter-normalized result는 exact contract, adapter가 immutable local call context에서
결속한 opaque request binding, expected segment set, 실제 전송 bytes에서 adapter가 계산한
segment/view fingerprint, `complete=true`, exact
`detector_contract_ref`와 category/confidence/bounded `rule_or_model_ref`를 가진 spans를
반환한다. Provider revision은 허용된 detector contract와 rule/model reference namespace를
고정하며 adapter는 응답 값을 server-approved bounded reference로 정규화한다. Unknown 또는
expected provider revision과 다른 detector/rule/model reference는 전체 response를
무효화한다. 이 reference는 raw provider 설명이나 prompt/model output이 아니며 exact span과
함께 audit/trace에 저장하지 않는다. 빈 span은 schema/fingerprint/coverage가 모두 유효한
complete result일 때만 성공이다. Missing/duplicate/unknown segment, wrong fingerprint,
unsupported category, NaN/Infinity confidence, reversed/out-of-range/UTF-8-middle span,
response/span cap 초과는 전체 result를 무효화한다. 유효한 일부 segment를 사용하지 않는다.

Segment byte/overlap, request/response/span count, timeout와 concurrency의 초기 exact
cap은 MBA-362가 provider 공식 제한과 부하 근거를 바탕으로 code-owned versioned
constant와 contract test에 고정한다. 해당 cap과 readiness test가 없으면 provider path는
활성화하지 않는다. Organization/provider 설정은 platform cap을 낮출 수만 있다.

### 6. External provider egress와 credential

`external_approved` adapter는 baseline 검출 범위를 safe placeholder로 치환하거나
제거한 provider-safe view만 전송한다. View offset과 document offset의 map은
attempt/process memory에만 둔다. Provider span이 제거된 구간을 가로지르면 원문상의
보수적 covering range로 확장하거나 전체 응답을 거부하며 안전한 부분만 선택해
축소하지 않는다. Provider는 baseline span을 복구하거나 약화할 수 없다.

`provider-safe`는 baseline으로 확인된 값을 제거한 최소화 projection이라는 뜻이며
민감정보가 없다는 보증이 아니다. Hard baseline의 미탐, 문맥과 조직 기밀이 남을 수 있다.
따라서 `external_approved`는 위 exact Detector Egress Approval Revision과 중앙 egress guard를
모두 만족해야 한다. View 또는 detector
결과를 `clean`, compliance pass나 raw 외부 전송 승인으로 재사용하지 않는다.

Application-to-adapter request의 Organization id, raw document fingerprint, policy/provider
내부 revision은 외부 wire payload가 아니다. Adapter는 실제 전송 bytes와 immutable local
call context에 normalized result를 결속한다. Provider protocol이 custom correlation field를
공식 지원하고 registration contract가 승인한 경우에만 per-call opaque key 또는 view
fingerprint를 최소 allowlist로 전송할 수 있다. Provider echo를 필수로 요구하거나 단독 무결성
근거로 신뢰하지 않는다. Raw source URL/path/title, user principal/email, ACL row, credential, connector
exception과 불필요한 document metadata는 보내지 않는다.

Detector egress는 trust tier별로 분리된 operation profile과 transport를 사용한다.

- `external_approved`는 ADR-0067의 중앙 public-address Outbound Egress Guard를 그대로
  사용한다. 모든 DNS 결과의 public-address 검증, 검증 주소 pinning, peer/Host/TLS SNI 일치,
  redirect/proxy 금지와 bounded size/timeout을 완화하지 않는다.
- `organization_private`는 generic public guard의 private-address 예외가 아니다. 별도
  `knowledge.detector.organization_private` operation profile과 dedicated transport/network
  isolation이 server-owned exact provider revision의 host/port 및 exact CIDR/address allowlist를
  고정해야 한다. 모든 DNS 결과가 그 allowlist 안에 있어야 하며 loopback, link-local, cloud
  metadata, multicast, unspecified, public 또는 승인되지 않은 private address가 하나라도 있으면
  차단한다. 검증 주소 pinning과 peer/Host/TLS SNI 일치, HTTPS+mTLS, no redirect/proxy/userinfo/
  arbitrary header를 강제한다. Dedicated worker/network namespace 또는 NetworkPolicy가 허용
  destination 외 egress를 차단해야 하며 generic private dial을 제공하지 않는다.
- Ingestion/runtime request는 endpoint, CIDR, profile 또는 transport를 제출하지 않는다. Authorized
  provider management plane은 deployment/change-control operator가 server-owned registry에 확정한
  private profile만 참조해 immutable provider revision을 bind할 수 있고 Organization actor가 endpoint,
  CIDR/profile/transport를 만들거나 확장하지 못한다.
  Approval revision, private IP/DNS 또는 mTLS 하나만으로 transport readiness가 되지 않는다. Dedicated
  profile과 network isolation readiness가 없으면 policy activation과 outbound call을 모두
  fail-closed한다.
- 두 profile 모두 server-owned immutable provider revision에서 endpoint와 credential capability를
  얻고 DB session, transaction과 row lock을 닫은 뒤 network I/O를 수행한다. Raw request/response,
  endpoint, provider request id와 exception은 log/trace/audit에 남기지 않는다.

이 결정은 ADR-0067의 public-address guard를 완화하지 않고 Knowledge private detector라는 이름 있는
operation에만 별도 private transport를 추가한다.

External parser는 detector보다 먼저 raw bytes를 받으므로 Detector Egress Approval Revision을
재사용하지 않는다. Default parser는 network가 없는 local isolated parser다. External parser를
사용하려면 Organization과 applicable ingestion scope가 명시적으로 opt-in해야 한다. Source-managed
document는 exact source scope, manual document는 exact KB scope를 사용하며 source gate를 서로
합성하지 않는다. Exact immutable Raw Parser Egress Approval Revision은 Organization, source 또는 KB,
parser revision, purpose, processor/ownership, endpoint/network boundary,
residency, retention, no-training/no-secondary-use와 active/expiry/revoke를 고정해야 한다. Server-owned
parser revision과 credential capability, parser 전용 `knowledge.parser.external_approved` operation
profile, ADR-0067 public-address guarded transport,
request/response size·timeout·concurrency, no-log/no-durable-payload contract 및 readiness를 모두
검증한다. V1은 approved public HTTPS parser만 지원하며 private raw parser destination은 지원하지
않는다. Private parser가 필요하면 별도 Accepted ADR과 이름 있는 dedicated isolation profile이 먼저
필요하다. Client가 parser endpoint/config/credential/approval을 선택할 수 없다.

현재 `llamaparse`의 Organization credential/`use` 검증만으로는 이 승인을 충족하지 않는다. MBA-362
또는 이름 있는 후속 구현이 management, approval, transport와 테스트를 닫기 전 Target enforcement는
`llamaparse`를 포함한 external parser strategy를 upload 전에 `knowledge.raw_parser_egress_unavailable`로
차단한다. 승인된 parser output도 untrusted input이며 local normalization과 hard baseline을 반드시
통과한 뒤에만 Organization detector, chunking 또는 embedding으로 진행한다.

LLM-backed detector는 ADR-0064의 기존 generation purpose를 암묵적으로 재사용하지
않는다. 별도 provider execution capability/purpose, 비용·timeout·prompt·response
projection이 승인되기 전에는 지원하지 않는다.

### 7. Local masking과 canonicalization

Shared Privacy Core는 다음 순서로 결과를 확정한다.

1. 모든 baseline/provider span과 request binding을 검증한다.
2. Provider-view span을 normalized document byte range로 환산한다.
3. Document range로 정렬하고 overlap/duplicate를 union한다.
4. Platform minimum보다 강한 `block > manual_review > mask` action을 보존한다.
5. Category를 드러내지 않는 fixed `[REDACTED]` replacement를 적용한다.
6. Staged redacted candidate bytes와 safe structure/metadata, candidate-bound privacy decision manifest를 만든다.

동일 contract/input/policy/span set은 byte-for-byte 동일한 staged candidate를 생성한다.
Provider가 만든 redacted text, LLM rewrite 또는 category별 replacement를 canonical source로
사용하지 않는다. Raw-to-redacted map, exact span과 confidence는 ephemeral이다.

Knowledge Ingestion Application은 effective action이 manual review를 요구하면 exact candidate revision을
`pending_review`로 유지한다. Review가 필요하지 않은 candidate 또는 exact current revision approval을
통과한 candidate만 approved immutable revision으로 승격해 redacted canonical content와 active artifact의
입력으로 사용한다. Candidate-bound manifest는 승인 전에도 provenance로 존재할 수 있지만 retrieval authority가
아니며 approved exact candidate의 manifest만 finalization에 사용할 수 있다. Mask command가 만든 새 candidate는 local hard baseline을 다시 통과하더라도 자동 승인하지
않고 새 exact revision의 별도 승인을 요구한다.

Canonicalization contract, effective scoped privacy policy, baseline/provider/detector-approval/raw-parser
revision·approval, action policy, platform/Organization validity vector와 masking version은
ADR-0065의 canonical materialization input과 resolver/materialization fingerprint가
재현할 수 있는 immutable input이다. 이 중 하나가 바뀌면 새 canonical content
generation과 reindex 판단이 필요하다.

Chunk, derived representation, embedding/index와 retrieval-visible artifact는 redacted
canonical content가 성공한 뒤에만 생성한다. Finalizer는 redacted canonical content,
chunks, embeddings/index, privacy/effective-policy/detector·parser approval 및 platform/Organization
validity snapshot, current actor/source/parser/provider authority와 fencing을 검증한 뒤 ready/current/active pointer를
원자적으로 전환한다.

### 8. Logical data와 retention

Physical table/column은 MBA-362의 additive migration에서 확정한다. Target logical
entity는 다음 책임을 분리한다.

| Entity | 최소 safe contract |
| --- | --- |
| Organization/Scoped Privacy Policy Revision | Organization base 또는 Collection/source/KB stricter scope, closed requirements/action matrix, baseline/action/masking/normalization, nullable exact provider revision, immutable lifecycle |
| Collection Privacy Policy Binding Revision | Same-Organization active routing membership과 exact published Collection privacy policy revision을 KB에 명시적으로 결속하는 immutable protected revision. Organization manager impact acknowledgement와 expected-revision CAS로만 생성/교체/제거한다. |
| Effective Privacy Policy Snapshot | platform/Organization/정렬된 applicable Collection/source/KB exact revision refs, Collection privacy/source/KB binding revisions, compiled digest와 derived mode/action/provider |
| Detector Provider Registration/Revision | Organization, kind/trust tier, opaque endpoint/config/credential capability, exact egress approval revision, supported contract, active/revoked lifecycle |
| Detector Egress Approval Revision | Organization/provider/trust tier, purpose, processor/ownership·endpoint/network boundary, residency/retention/no-training/no-secondary-use와 active/expiry/revoke lifecycle |
| Raw Parser Egress Approval Revision | Organization/applicable source 또는 KB/parser revision, raw-parser purpose와 processor/ownership·endpoint/network, residency/retention/no-training/no-secondary-use, credential capability, `knowledge.parser.external_approved` public-address profile과 active/expiry/revoke lifecycle |
| Privacy Artifact Validity Revision | `platform|organization` scope, monotonic positive `validity_epoch`, previous/current revision, server-derived transition effect와 immutable effective time. Platform scope는 global baseline minimum, Organization scope는 해당 Organization policy/provider/approval minimum을 소유한다. |
| Privacy Detection Attempt | document/generation, effective policy/parser/provider/approval/baseline 및 exact platform/Organization validity revision/epoch snapshot, claim/fence, safe state/reason/timestamps/buckets |
| Privacy Review Candidate Revision | Pending generation의 encrypted staged redacted candidate ref, monotonic candidate revision, generation-bound `retention_expires_at`, legal-hold/purge state와 effective policy/Collection privacy binding/validity/source snapshot. Raw content, exact detector span, reversible map 또는 submitted mask range를 저장하지 않는다. Successor revision은 최초 generation expiry를 연장하지 않는다. |
| Privacy Review Decision | Exact generation/candidate revision, actor, `mask|approve|reject`, current policy/binding/validity/source precondition, safe outcome과 nullable successor candidate ref를 결속한 append-only record. Candidate body/range/raw/span/digest를 저장하지 않는다. |
| Privacy Decision Manifest | organization-scoped keyed source/span digest와 key version, redacted candidate ref와 exact candidate revision, effective policy snapshot/digest, provider/detector-approval/raw-parser-approval/baseline/masking refs, exact platform/Organization validity revision refs와 epoch snapshot, segment coverage digest, bounded outcome buckets. Approved exact candidate의 manifest만 canonical finalization에 사용한다. |
| Privacy Migration Inventory/Wave | non-authoritative bounded staging, frozen Organization/KB/document/nullable-version/exact `legacy_artifact_ref` inventory, legacy snapshot revision, exact platform/Organization validity revision ref+epoch, enforcement epoch/time, `privacy_legacy_grace_v1`, admission deadline/retrieval cutoff, irreversible eligibility retirement와 cleanup receipt |
| Raw Copy Cutover Inventory/Disposition | Nodease storage에 남은 exact upload/fetch raw copy inventory와 `protected_migrated|purged` terminal disposition. Protected destination 또는 purge receipt, original-copy physical-absence verification과 safe opaque correlation만 보존하며 raw body/object key는 기록하지 않는다. |

Raw bytes, parsed/normalized raw text, provider-safe view/map, exact byte-binding fingerprint,
exact spans/confidence, parser/provider request/response와 exception은 durable attempt/manifest,
job/retry/dead-letter, audit/trace/log에 저장하지 않는다. Low-entropy content의
equality/dictionary leakage를 줄이기 위해 durable source/span identity에는
Organization-scoped keyed digest와 key version을 사용한다. Audit/trace에는 digest도
넣지 않고 opaque manifest/attempt ref와 safe outcome만 사용한다.

Durable digest는 provider/LLM credential과 분리된 Privacy Digest Key Ring이 소유한다.
V1은 secret manager 또는 equivalent protected runtime secret에서 얻은 master key version과
Organization UUID를 domain-separated HMAC-SHA-256으로 결합해 Organization key를 파생하고,
contract version과 length-delimited canonical bytes를 다시 HMAC-SHA-256한다. Exact framing과
domain label은 code-owned `privacy_digest_hmac_sha256_v1` golden fixture로 고정한다. Master/
derived key는 DB, provider wire, job, audit, trace와 log에 저장하지 않는다.

New admission은 current active key version을 attempt/materialization snapshot에 고정하고
없으면 provider/embedding 전에 readiness를 fail-closed한다. 같은 attempt의 retry/recovery는
그 version을 유지한다. Active version rotation만으로 in-flight attempt를 다른 key로 다시
digest하거나 stale 처리하지 않고 새 admission만 새 version을 사용한다. Snapshot key의 revoke/
destruction은 next external batch와 finalization을 차단한다. Rotation은 기존 manifest를 eager
rewrite하거나 그 자체로 reindex하지 않는다. Historical key retention은 해당 manifest와
in-flight attempt의 retention/legal hold보다 짧을 수 없다. 예정된 key destruction은 영향
preview와 audit를 요구하며, 삭제된 historical digest의 검증 불가 상태가 기존 canonical
artifact를 권한 또는 content 재검증 없이 활성/비활성으로 바꾸지 않는다. Exact canonical
key-management audit action과 transaction ownership이 ADR-0008/API/test에 승인되기 전에는
destructive key management surface를 활성화하지 않는다.

Raw 보존은 ADR-0014의 opt-in protected raw artifact만 허용한다. 원본 source system이
자체 보유하는 source-of-record object와 Nodease의 opaque protected source identity/reference는
Nodease가 보존하는 raw content copy와 구분한다. 반면 upload 또는 connector fetch로 Nodease
저장소에 복사한 원문 bytes는 `file_path` 같은 기존 필드명과 무관하게 raw content이며 Target에서
protected raw artifact 계약을 따라야 한다. 검출을 위해 fetch한 bytes는 isolated ephemeral
handle로만 다루고 attempt/job/retry/dead-letter에 복제하지 않는다. Protected raw artifact는
encryption, raw/compliance permission, fresh source ACL, access audit, retention/legal hold/purge가
모두 닫혀야 하며 RAG/embedding/prompt/answer/citation input으로 사용하지 않는다.

Enforcement 전 Raw Copy Cutover Coordinator는 Nodease가 보유한 기존 upload/fetch 원문 copy를
exact inventory로 freeze한다. 각 item은 다음 중 하나로만 terminal 처리한다.

1. Organization/source opt-in이 유효하고 적용 가능한 retention/legal hold가 보존을 허용하거나 요구하면 protected store에 암호화 이관하고
   destination integrity와 기존 storage copy의 physical absence를 확인한 뒤 `protected_migrated`
   receipt를 commit한다.
2. Protected migration 조건을 충족하지 않고 legal hold가 삭제를 막지 않으면 기존 copy를 non-readable로 fence하고 물리 삭제한 뒤
   absence verification과 `purged` receipt를 commit한다.

Unknown/partial disposition, duplicate readable copy, failed absence verification과 legal-hold conflict는
cutover readiness를 fail-closed한다. Raw response를 차단하거나 DB의 `file_path`만 비우는 것으로
storage disposition을 대체하지 않는다. Receipt와 audit에는 safe opaque ref/state만 허용하고 storage
object key, content hash와 raw body는 넣지 않는다.
Organization별 exact inventory의 모든 item이 terminal disposition과 original-copy absence에 수렴한 뒤에만
final readiness marker와 `knowledge.raw_copy_cutover.completed` Audit Outbox intent를 같은 transaction에서
확정한다. `actor_type=system`, `actor_id=null`, safe Organization/opaque cutover ref와 bounded disposition
count bucket만 허용하고 item/document/storage identity와 exact count는 기록하지 않는다. Audit 준비 또는
commit이 실패하면 readiness를 활성화하지 않으며 per-item receipt를 canonical AuditLog로 중복 생성하지 않는다.

Attempt/view/map/response는 attempt 종료와 crash recovery에서 폐기한다. Safe operational
attempt/manifest retention은 구현 전에 bounded 기간과 cleanup owner를 명시한다.
Privacy Review Candidate body는 missed PII/secret 가능성이 있는 protected staged artifact로 취급한다.
V1 code-owned hard maximum `privacy_review_candidate_ttl_v1 = 7 * 24 hours`는 최초 candidate 생성의
authoritative DB time부터 계산하며 successor mask revision, Organization 설정과 client request가 연장하지
못한다. Approve transaction은 exact candidate를 review projection/mutation에서 닫되 encrypted body를
`approved_pending_finalization` input으로 유지한다. Finalizer가 같은 candidate, authority, policy, validity와
fence를 다시 검증해 canonical active artifact를 commit한 transaction에서만 body를 `purge_pending`으로
전환한다. 그 전의 transient finalization 실패는 TTL 안에서만 동일 immutable input으로 재시도할 수 있다.
`db_now >= retention_expires_at`, reject, canonical finalization commit, successor 확정, generation에 결속된
source/ingestion authority revoke 또는 stale/abandoned generation은 해당 candidate body를 즉시
non-projectable, non-mutable `purge_pending`으로 만든다. 개별 reviewer의 manager/source-display 권한 회수는
그 actor의 projection/mutation만 hidden zero-write로 닫고 다른 authorized reviewer가 사용할 candidate를
purge하지 않는다. Knowledge-owned cleanup reconciler는 current cleanup claim/fence를
확인해 bounded batch로 physical body를 24시간 안에 purge하고 durable receipt/tombstone을 확정한다.
Legal hold는 physical deletion만 보류하며 review projection, approval 또는 TTL을 재개하지 않는다.
Append-only Privacy Review Decision, safe canonical audit, manifest provenance와 purge receipt는 candidate
body와 분리 보존한다. Encrypted staging store, expiry enforcement, legal-hold handling, fenced cleanup,
receipt/reconciliation readiness가 모두 없으면 review-capable mode/action을 활성화하지 않는다.
Provider revision은 historical provenance를 위해 immutable/tombstone reference를
보존하되 credential/config secret은 해당 owner의 rotation/purge 계약을 따른다.

### 9. Failure, retry, cancellation과 race

| 실패 | Safe outcome | 후속 I/O/visibility |
| --- | --- | --- |
| policy missing/stale | `knowledge.privacy_policy_unavailable` | provider/embedding 0회, no active swap |
| protected raw parser approval/profile/readiness missing | `knowledge.raw_parser_egress_unavailable` | external parser upload/provider/embedding 0회 |
| baseline failure | `knowledge.privacy_baseline_failed` | provider/embedding 0회 |
| baseline/provider action policy의 terminal block | `knowledge.sensitive_content_detected` | baseline block은 provider 0회, 모두 no embedding/finalization |
| provider missing/inactive/revoked/timeout | `knowledge.detector_provider_unavailable` | no embedding, quarantine |
| malformed/fingerprint/span/response-cap failure | `knowledge.detector_response_invalid` | partial result 폐기 |
| manual review/action | `knowledge.privacy_review_required` | review pending, no embedding |
| normalized input platform cap 초과 | `knowledge.privacy_input_too_large` | provider/embedding 0회 |
| actor/source/provider/policy revoke race | existing safe authorization/stale cancellation code | no next batch/finalization |

위 값은 raw-free job/status/policy reason code다. 기존 HTTP status/error envelope을 이
ADR만으로 일괄 변경하지 않는다. Scope 밖 또는 hidden resource는 ADR-0010/ADR-0017의
safe hiding을 먼저 적용하며 privacy/provider identity를 reason으로 노출하지 않는다.

Provider 호출은 순수 detection이라도 비용과 outcome-unknown이 있으므로 stable attempt
request identity와 bounded retry만 사용한다. 동일 materialization tuple은 하나의 valid
claim generation만 provider 호출 권한을 가진다. Stale lease/fence worker는 progress,
outcome, cleanup 또는 finalization을 commit하지 않는다. Provider call 중 cancellation,
policy/provider revoke 또는 actor/source authorization change가 commit되면 in-flight call을
성공 근거로 사용하지 않고 다음 batch와 active pointer swap을 차단한다.

Provider가 같은 input/revision에 다른 valid span set을 반환할 수 있음을 계약에 포함한다.
Validated spans, local-masked staged redacted candidate와 candidate-bound Privacy Decision Manifest는 current
fence 아래 하나의 transaction으로 staging commit한다. Exact spans는 commit하지 않고
span-set digest만 manifest에 남긴다. Staging candidate commit 뒤 retry/recovery는 provider를
다시 호출하거나 다른 response와 union/majority-select하지 않고 committed candidate를
재사용한다. Candidate commit 전 crash 또는 lease takeover만 새 bounded call을 허용하며,
겹친 이전 generation response는 fence 때문에 commit할 수 없다. 따라서 determinism은
`same accepted span set -> same staged candidate bytes`에 한정하고 provider 자체의 결정론을
주장하지 않는다.

Artifact가 과거에 privacy gate를 통과했다는 사실과 현재 retrieval에 사용할 수 있다는 사실은
분리한다. V1은 per-artifact bulk update 대신 global platform epoch와 per-Organization epoch의
두 요소로 validity를 판정한다. Effective scoped policy/baseline/provider/detector-approval/
raw-parser revision·approval transition은 server-owned compatibility validator가 다음 둘 중 하나로
분류한다.

- `artifact_preserving`: detection 의미와 platform minimum을 약화하지 않는 credential rotation,
  operational endpoint 교체 같은 변경이다. Current validity revision은 전진할 수 있지만 해당
  scope의 `validity_epoch`은 유지한다. 기존 manifest는 epoch equality로 current-valid일 수 있고 새
  admission/finalization은 새 exact revision을 사용한다.
- `artifact_invalidating`: baseline security fix/ruleset supersession, stronger action 또는 새 required
  detector처럼 기존 canonical content가 현재 최소 정책을 만족한다고 증명할 수 없는 변경과 provider/
  egress approval의 security invalidation이다. Global baseline minimum 변경은 platform epoch을,
  Organization/scoped effective policy, explicit Collection privacy/source/KB binding, provider/detector-approval/
  raw-parser security 변경은 해당 Organization epoch을 정확히 1 증가시킨다.
  Unknown/ambiguous transition도 이 값으로 처리한다.

`artifact_invalidating` activation은 affected scope의 current Privacy Artifact Validity Revision과
epoch CAS, canonical management audit를 같은 authoritative transaction에 확정한다. V1 Organization
scope invalidation은 선택적 manifest 목록을 추측하지 않고 그 Organization의 모든 privacy-gated
artifact를 보수적으로 무효화한다. 더 세밀한 provider/document scope는 별도 ADR 없이는 추가하지
않는다. V1은 Organization/client가 지정하는 grace를 두지 않으며 commit 시점부터 old epoch manifest를
retrieval-ineligible로 만든다.
Active pointer는 historical/build reference로 남을 수 있지만 prefilter와 final evidence gate는
current platform/Organization epoch을 재검증해 stale cache/vector evidence까지 제외한다. Routine credential rotation,
temporary provider disable과 future-admission-only revoke는 명시적으로 `artifact_preserving`임이
검증될 때만 historical artifact를 무효화하지 않는다. Exact management action과 transaction owner가
승인되지 않은 동안 provider/policy mutation surface는 비활성이다.

새 generation 실패, quarantine, cancellation 또는 stale 상태는 current-valid인 기존 compliant
active-ready version만 retrieval에 유지한다. `artifact_invalidating` transition으로 current validity를
잃은 version은 새 generation 실패와 무관하게 사용할 수 없다. Legacy raw-derived artifact는 별도
전환 대상으로 표시하며 자동으로
compliant 또는 rollback-safe version으로 승격하지 않는다.
아래의 명시적 pre-cutoff migration 예외 밖에서 기존 compliant active-ready version이 없으면
document는 retrieval-unavailable 상태를 유지하고 legacy/raw/staging/partial artifact로
fallback하지 않는다.

무중단 전환이 필요한 경우에만 enforcement 시작 전에 서버가 고정한 immutable migration
inventory/wave에 포함된 pre-cutoff artifact를 `legacy_unverified`로 표시해 정해진 cutoff까지
기존 retrieval pointer로 유지할 수 있다. 이는 privacy mode, compliant artifact 또는 실패
fallback이 아니며 request, graph, document metadata나 runtime command로 선택할 수 없다. Wave는
Organization/KB/document/nullable version, exact opaque `legacy_artifact_ref`, inventory revision,
freeze 시점의 exact global platform 및 same-Organization validity revision ref/epoch,
admission deadline과 cutoff를 고정하고 새 ingestion/reprocess 결과를 추가 등록하거나 client가
기한을 연장할 수 없게 한다. `legacy_artifact_ref`는 versioned 또는 unversioned chunk와
vector/keyword/hierarchy generation의 exact frozen set을 결속하며 raw content나 content digest를
외부 projection에 노출하지 않는다. Pre-cutoff reindex 실패는 기존 legacy pointer를 바꾸지
않지만 cutoff 뒤 compliant active-ready version이 없으면 retrieval-unavailable로 전환한다.
Legacy cleanup receipt가 확정된 artifact는 다시
활성화하거나 rollback 대상으로 사용할 수 없다.

Privacy-compliant active pointer를 finalize하는 transaction은 같은 document의 frozen legacy
eligibility를 함께 비가역적으로 retire한다. 한 번 compliant pointer가 확정된 document는 cleanup이
아직 끝나지 않았거나 이후 active manifest가 stale/invalid가 되더라도 legacy eligibility를 다시
얻지 못한다. Active pointer가 current-valid가 아니면 retrieval-unavailable이며 legacy artifact로
fallback하지 않는다. Legacy retrieval은 compliant active pointer를 한 번도 확정하지 않은 document가
frozen wave membership과 cutoff를 모두 통과할 때만 허용한다.

Legacy physical cleanup은 ADR-0065의 artifact purge completion 계약을 재사용한다. Pre-delete
transaction이 exact `legacy_artifact_ref`를 non-retrievable `purging`으로 fence하고 durable
cleanup intent를 확정한 뒤에만 외부 chunk/vector/keyword/hierarchy generation을 삭제한다.
Physical absence를 확인한 completion transaction은 cleanup receipt, append-only tombstone과
canonical `knowledge.processing_artifact.purged` audit를 함께 확정한다. Completion commit 실패는
legacy를 visible로 되돌리지 않고 same-generation reconciler가 exactly-once로 완성한다.

`admission_deadline_at`은 해당 wave에서 새 reindex attempt를 받을 수 있는 마지막 시각이고,
`retrieval_cutoff_at`은 legacy pointer가 retrieval-visible할 수 있는 마지막 시각이며 전자는
후자보다 늦을 수 없다. 각 artifact는 enforcement 전에 최대 한 wave에만 배정되고 더 늦은
wave로 이동할 수 없다. Deadline 전에 적법하게 admitted된 in-flight attempt는 cutoff 뒤에도
fresh actor/source/policy/provider/approval, current platform/Organization validity revision/epoch과
fence를 통과하면 compliant artifact를 finalize할 수 있지만,
그때까지 legacy retrieval은 재개하지 않는다.

V1 code-owned hard maximum은 `privacy_legacy_grace_v1 = 30 * 24 hours`다. Immutable wave의
`retrieval_cutoff_at`은 `enforcement_activated_at + 30 * 24 hours`보다 늦을 수 없고
`admission_deadline_at <= retrieval_cutoff_at`을 함께 만족해야 한다. Deployment-controlled rollout은
이 상한을 줄일 수만 있고 늘리거나 Organization별 다른 상한을 만들 수 없다. Supported grace
contract와 immutable enforcement activation time을 readiness에서 증명하지 못하면 wave를
활성화하지 않는다.

두 시각은 UTC instant이고 authoritative database transaction time으로 비교한다. Admission과
legacy retrieval은 각각 `db_now < admission_deadline_at`,
`db_now < retrieval_cutoff_at`일 때만 허용하며 equality부터 닫힌다. Scheduler/worker가 cutoff
state를 갱신하지 못해도 legacy evidence를 계속 제공하지 않는다. Retrieval prefilter와 final
evidence gate가 frozen wave membership, current KB/source permission, cutoff 및 frozen platform/
Organization validity epoch와 current 두 epoch의 equality를 모두 재검증하고, stale cache/vector
result도 final gate에서 제외한다. `artifact_invalidating` transition이 current platform 또는
Organization epoch을 전진시키는 commit 자체가 관련 legacy eligibility를 즉시 닫는다. Item bulk update나
cleanup 완료를 기다리지 않으며 `artifact_preserving` transition만 same-epoch legacy eligibility를
cutoff 안에서 유지할 수 있다.

대규모 inventory item은 bounded batch로 staging할 수 있지만 frozen wave 전에는 권위가 없고
legacy eligibility를 부여하지 않는다. Organization privacy rollout coordination row를 잠근 최종
freeze/activation transaction은 exact current retrieval-visible raw-derived set과 staged inventory를
대조하고 `legacy_snapshot_revision`, enforcement epoch와 rollout marker를 전진시킨 뒤 immutable
wave header와 canonical audit를 확정한다. Legacy-producing pointer writer는 같은 epoch를 CAS한다.
Freeze가 이기면 해당 writer는 legacy commit을 하지 않고 새 privacy gate로 fresh admission하며,
writer가 먼저 이기면 freeze는 inventory를 다시 대조해야 한다. Partial staging/freeze 실패는
enforcement를 켜거나 `legacy_unverified` eligibility를 만들지 않는다. Aborted/unfrozen staging은
bounded cleanup 대상이며 frozen wave/receipt와 혼동하지 않는다.

Deployment create with `is_active=true`, enable/toggle과 standalone preflight는 runtime과 같은
retrieval-visible resolver를 사용한다. Current-valid compliant active manifest가 있으면 허용하고,
compliant active pointer가 한 번도 확정되지 않은 document의 frozen pre-cutoff legacy artifact만 아직
retire되지 않았고 cutoff 전이며 frozen platform/Organization validity epoch가 current 두 epoch와 같을 때
예외로 허용한다. Missing/stale/invalid manifest, validity epoch가 stale한 legacy, compliant pointer 뒤
남은 legacy, cutoff가 닫힌 legacy 또는 retrieval-visible artifact 부재는 unresolved로 계산한다.
이 availability 결과는 safe `knowledge_privacy_artifact_unavailable`과
`reprocess_or_remove_unavailable_knowledge`로 정규화하고 document/policy/provider identity와 exact count를
노출하지 않는다.
Standalone `POST /api/v1/deployments/preflight`는 blocked 결과도 `200 OK`와 `status="blocked"`로
반환하고, `is_active=false`에서 inactive 저장이 허용되는 availability blocker만 기존 Deployment
계약에 따라 safe `warning`으로 낮출 수 있다. Active create/enable/toggle은 같은 unresolved 결과를
`409 deployment.preflight.blocked`로 차단하며 hidden document/provider/policy identity와 exact count를
노출하지 않는다.

### 10. Audit와 observability

- Runtime privacy block은 ADR-0008의 `policy.block`과 최상위
  `audit_metadata.policy_reason=knowledge.sensitive_content_detected`를 사용한다.
- Migration principal이 immutable inventory/wave를 만드는 성공 mutation은 ADR-0008의
  `knowledge.privacy_migration_wave.created`를 final frozen inventory validation, enforcement
  epoch/rollout marker와 같은 Unit of Work에 기록한다. `actor_type=system`, `actor_id=null`,
  safe Organization id, opaque wave ref, deadline/cutoff, policy revision ref와 bounded
  item-count bucket만 허용한다.
- Legacy physical cleanup completion은 ADR-0065/ADR-0008의
  `knowledge.processing_artifact.purged`를 cleanup receipt와 append-only tombstone의 같은
  transaction에 한 번 기록한다. Audit에는 safe Organization/wave/receipt/tombstone ref와 fixed
  reason만 허용하고 internal `legacy_artifact_ref`와 exact membership은 넣지 않는다. Pre-delete
  fence/intent는 success action을 만들지 않는다.
- Terminal blocked attempt의 safe state와 session-bound generic audit Outbox intent는 같은
  Unit of Work에서 확정한다. Outbox/audit preparation 실패는 attempt terminal state,
  artifact와 active pointer를 부분 commit하거나 raw payload fallback을 만들지 않는다.
- Normal successful detection은 high-cardinality AuditLog를 만들지 않고 bounded operational
  state/metric으로 관측한다.
- Safe allowlist는 Organization/document/version의 권한 검증된 opaque ref, safe state/reason,
  effective-policy/baseline/parser/provider contract의 비민감 revision ref, latency/count bucket, retryability와
  correlation을 포함할 수 있다.
- Raw/canonical/chunk body, exact span/confidence/count, source/canonical/span digest, endpoint,
  parser/provider/credential identity/config/request id, parser/provider exception, internal
  `legacy_artifact_ref`와 exact migration membership은 포함하지 않는다.
- Trace redaction은 defense-in-depth이며 privacy gate 성공 증거가 아니다.

Scoped policy/Collection privacy binding/provider/raw-parser control-plane과 manual review mutation의 exact canonical action, transaction
ownership과 audit failure rollback은 해당 관리 surface 구현 전에 ADR-0008과 API/test
문서에서 확정한다. Provider lifecycle/credential/egress-approval management와 canonical audit
경계가 없으면 non-null provider path를, review management와 canonical audit 경계가 없으면
provider ref 유무와 무관하게 review-capable mode/action을 각각 production에서 활성화하지 않는다.
한 readiness의 부재를 다른 경로의 silent downgrade 또는 무관한 경로 차단 사유로 사용하지 않는다.

### 11. Rollout

1. Additive policy/core/manifest schema, current platform validity revision/epoch과 readiness를 도입한다.
2. Enforcement는 비활성으로 유지한 채 initial policy/validity를 같은 authoritative Unit of Work에 쓰는
   dual-write-capable Organization writer를 먼저 배포한다. Signup, OAuth, seed/admin을 포함한 모든 creation
   path가 같은 writer generation으로 수렴했음을 확인하고, 구버전 writer Pod/process를 drain/fence한 뒤에만
   backfill을 시작한다. 이 writer fence를 보장할 coordinated rollout이 없으면 maintenance window를 사용한다.
   운영 drain만을 안전 근거로 삼지 않는다. Target DB는 migration 중 nullable인
   `privacy_foundation_writer_generation`과 initial policy/current Organization validity ref를 backfill한 뒤,
   activation coordination transaction에서 no-default non-null commit constraint로 전환한다. New writer는 이
   값을 명시적으로 쓰며 field/reference를 모르는 old writer insert는 Organization row를 남기지 않고 실패한다.
3. 구버전 writer가 더는 Organization을 만들 수 없는 상태에서 기존 Organization마다 current platform
   baseline/action/normalization/masking contract를 참조하는 server-owned explicit `baseline_only` policy
   revision과 initial Organization validity revision/epoch을 idempotent upsert한다. Concurrent new-writer create와
   backfill은 Organization row/uniqueness로 직렬화한다. Bounded rescan을 zero-missing까지 반복하고 exact
   writer-generation readiness marker와 invariant result를 commit한 뒤에만 enforcement activation을 허용한다.
   Migration 뒤 새 Organization creation도 같은 initial revision을 authoritative Organization creation Unit of
   Work에 포함한다. Bootstrap 실패는 migration/creation을 fail-closed하고 runtime missing policy/validity를
   암묵적 default로 해석하지 않는다. Enforcement 뒤 dual-write를 모르는 구버전 image의 startup/rollback은
   deployment readiness에서 거부하고, 이를 우회해도 DB writer-generation/foundation constraint가 insert를
   원자 거부한다. Exact physical FK/deferred-constraint shape는 MBA-362 migration에서 고정하되 server default로
   old writer를 통과시키는 구현은 허용하지 않는다.
4. 전환 대상 pre-cutoff artifact만 포함하고 freeze 당시 exact platform/Organization validity
   revision ref/epoch을 결속하는 immutable migration inventory/wave와
   `admission_deadline_at <= retrieval_cutoff_at <= enforcement_activated_at + 30 * 24 hours`를
   code-owned `privacy_legacy_grace_v1`로 서버가 확정한다. Inventory, rollout marker와
   `knowledge.privacy_migration_wave.created` audit는 Organization rollout coordination lock 아래
   final freeze/activation Unit of Work에서 확정한다. Bounded staging은 비권위이며 exact current
   legacy set과 일치하지 않으면 freeze하지 않는다. Legacy-producing writer는 같은 enforcement
   epoch를 CAS한다. Request/runtime enrollment, later-wave reassignment와 client deadline
   extension은 허용하지 않는다.
5. Nodease storage의 기존 upload/fetch 원문 copy를 exact inventory로 freeze하고 각 item을
   `protected_migrated|purged` terminal disposition과 original-copy physical-absence verification으로
   수렴시킨다. Unknown/partial disposition, legal-hold conflict 또는 readable duplicate가 남으면
   enforcement를 활성화하지 않는다. Raw response 차단만으로 이 readiness를 대체하지 않는다.
6. Raw-free shadow metric과 allowlisted Organization pilot을 수행한다.
7. Safe management/revocation/credential/audit surface가 없는 wave는
   provider ref가 없는 `baseline_only`까지만 활성화한다. Review-capable action/mode는 review
   management/audit readiness, non-null provider path는 exact egress approval와 credential readiness를
   추가로 요구한다.
8. Protected raw parser egress management/approval/operation-profile/network readiness가 없으면
   `llamaparse`를 포함한 external parser strategy를 비활성화하고 local isolated parser만 조립한다.
9. 새 ingestion에 enforcement를 적용한다.
10. Legacy document는 별도 generation으로 reindex하고 완성된 artifact만 active pointer와 legacy
   eligibility retirement를 원자적으로 교체한다.
11. Previous raw-derived chunk/vector/keyword/hierarchy generation은 non-retrievable purge
   fence/intent 뒤 제거하고 physical absence 확인 후 cleanup receipt, tombstone과
   `knowledge.processing_artifact.purged` audit를 원자 확정한다.

Pre-cutoff reindex 실패는 inventory에 고정된 legacy pointer를 유지하지만 cutoff 이후에는
compliant active-ready version이 없는 document를 retrieval-unavailable로 닫는다. Cleanup
receipt가 확정된 legacy artifact를 되살리는 rollback은 허용하지 않는다.

Effective scoped policy/provider/raw-parser/masking/normalization 변경은 새 materialization input이다. Publish 또는
배포만으로 전 조직 reindex를 자동 시작하지 않고 영향 preview/admission 계약을 사용한다.
다만 server-derived `artifact_invalidating` transition은 reindex 완료를 기다리지 않고 current
validity를 즉시 닫으며 기존 artifact를 availability fallback으로 사용하지 않는다.

### 12. Contract trace

| Invariant | Actor/precondition | Result/error | Audit/transaction | Required test |
| --- | --- | --- | --- | --- |
| protected parsing then baseline | authorized ingestion + content safety; local parser 또는 exact raw-parser approval/profile | raw-parser gate 실패는 upload 0회, parser output 뒤 baseline 실패/block은 detector/embedding 0회 | `knowledge.raw_parser_egress_unavailable`, `knowledge.privacy_baseline_failed` 또는 `knowledge.sensitive_content_detected`, raw-free | local/external parser order와 zero-call |
| effective scoped policy | current platform/Organization와 모든 explicitly bound Collection/source/KB revision/binding | weakest-wins/복수 provider/missing scope를 fail-closed | effective snapshot/digest와 invalidating Organization epoch 원자성 | scope precedence가 아닌 strongest-union/cross-scope race |
| routing/lifecycle과 privacy binding 분리 | Organization manager + active membership + exact impact/expected revisions | `catalog_manage` membership 및 `lifecycle_manage` archive/restore는 privacy 미변경, active binding이 있는 unlink/hard-delete/policy purge는 safe conflict | binding mutation+Organization epoch+management audit 원자성 | delegated membership/lifecycle mutation, bind/unbind/unlink CAS race |
| exact same-Organization provider | current immutable policy/provider revision | unavailable, no fallback | no secret/provider identity | cross-org/revoke |
| closed mode/provider/action matrix | policy publish/activate readiness | invalid combination 또는 review readiness 부재 시 enable 실패 | zero mutation/audit on validation failure | mode x provider x action table |
| exact egress approval | current provider + same-Organization approval revision + tier-specific operation profile | missing/stale/revoked/readiness 부재면 adapter 0회 | approval identity/detail 비노출 | public guard 유지/private dedicated transport lifecycle |
| external provider-safe view | egress guard + external trust tier | no baseline text egress | raw-free trace/log | canary/SSRF |
| UTF-8 span/fingerprint | exact contract/coverage | invalid result whole-fail | no exact span | Unicode/fuzz |
| local masking before embedding | complete validated span union | canonical or blocked/review | blocked attempt+audit Outbox 또는 manifest+artifact finalization | embedding/audit rollback spy |
| fresh actor/source/policy/parser/provider | external batch/finalize revalidation | cancel/stale, current-valid old active만 유지 | fenced transaction | race/PostgreSQL |
| current privacy validity | manifest의 exact platform/Organization validity refs+epochs가 current 두 epoch와 일치 | invalidating epoch CAS commit 즉시 old epoch unavailable | validity revision/epoch transition과 canonical management audit 원자성 | preserving same-epoch, invalidating +1, stale-cache와 cross-Organization matrix |
| one-way legacy transition | migration principal + exact frozen versioned/unversioned `legacy_artifact_ref` inventory + frozen platform/Organization validity epoch + rollout epoch/time + `privacy_legacy_grace_v1` | invalidating epoch commit 즉시 legacy unavailable, compliant active pointer swap과 eligibility retirement 뒤에는 stale active에서도 legacy 비복귀 | freeze/activation audit, pointer+retirement, cutoff와 cleanup receipt | preserving/invalidating epoch, wildcard/partial-set, writer/freeze race, max boundary, active-pointer no-fallback |
| source-gated manual review | Organization manager + exact non-expired candidate + fresh source display authorization | projection/commit 전 revoke 또는 TTL equality는 hidden/non-mutable; range-only mask는 최초 expiry를 유지한 새 pending revision; exact approve만 finalize | candidate state/revision mutation과 canonical review audit 원자성, loser audit 0건 | no-provider manual mask, blanket/stale/expired approval, approve/mask/revoke race |
| review candidate cleanup | Knowledge cleanup owner + current purge claim/fence + DB-time expiry/terminal state | approve 뒤 finalization input은 TTL 안에서만 유지하고 canonical finalization/expiry/reject/successor/generation authority revoke 뒤 body를 즉시 non-projectable로 전환; legal hold는 deletion만 보류 | append-only decision/audit/manifest는 body와 분리 보존 | approve-finalize-cleanup race, 7-day equality, successor no-extension, reviewer revoke와 generation authority revoke 분리, stale-worker/legal-hold matrix |
| existing raw-copy disposition | deployment-owned cutover principal + exact frozen Nodease raw-copy inventory | valid opt-in과 retention/legal-hold 보존 조건을 모두 충족하면 protected migration, 그 조건을 충족하지 않고 hold가 삭제를 막지 않으면 non-readable fence 뒤 purge; no-opt-in hold/unknown/duplicate/absence 미확인은 activation block | all-terminal readiness와 canonical completion audit 원자성, safe opaque disposition receipt, raw/object key 비노출 | active/expired opt-in, retention expiry, legal-hold/purge/partial-copy, audit rollback과 response-block-only negative case |
| deployment privacy preflight | deployment actor + server-derived runtime audience | runtime-visible current-valid artifact가 없으면 `knowledge_privacy_artifact_unavailable`, active deployment blocked | safe fixed reason/action only | stale manifest, retired/expired legacy, no active artifact |
| management before non-null provider enable | provider lifecycle/credential/egress-approval/audit readiness | non-null provider path enable blocked | no unaudited mutation | independent provider-readiness rollout test |
| management before review enable | review lifecycle/audit readiness | review-capable mode/action enable blocked | no unaudited mutation | independent review-readiness rollout test |
| Organization bootstrap writer fence | all creation paths on dual-write generation, old writer drained/fenced, zero-missing rescan, no-default DB foundation constraint | missing policy/validity 또는 old writer 재진입 시 enforcement disabled/startup/insert blocked | readiness marker, generation/foundation refs와 bootstrap rows 원자성 | create-between-scan, concurrent upsert, old-image rollback/insert |

## Consequences

- Privacy core와 provider adapter/application orchestration이 분리된다.
- Organization은 platform baseline을 유지한 채 자체 DLP/NER를 강화할 수 있다.
- Provider 장애가 ingestion availability를 낮출 수 있지만 보호 수준을 조용히 낮추지
  않는다.
- Unicode/span, provider lifecycle, keyed digest, manual review와 legacy reindex를 위한
  추가 schema와 테스트가 필요하다.
- Security-invalidating privacy transition은 새 generation 성공 전에도 과거 artifact의 retrieval을
  닫을 수 있으므로 availability보다 current minimum 보호를 우선한다.
- V1 Organization invalidation은 per-provider 선별 무효화보다 보수적인 Organization-wide epoch
  전진을 사용한다. 단일 CAS로 race를 닫는 대신 영향 범위가 넓을 수 있다.
- LLM detector를 사용하지 않는 Organization도 local hard baseline과 exact-candidate 수동 masking/승인을
  통해 compliant artifact를 만들 수 있다. 이는 terminal block이나 raw/compliance access를 우회하지 않는다.
- Detector는 false positive/negative가 있으므로 100% 제거를 보장하지 않는다. 언어와
  문서 유형별 precision/recall, false-positive/negative, quarantine/review rate, latency와
  cost를 평가한다.

## Implementation Status

- Policy/document contract: 이 ADR과 관련 공식 문서에서 Accepted Target.
- Current runtime/persistence/provider adapter: 미구현, MBA-362.
- Ingestion application/adapter 구조 개선: MBA-298과 조율하되 이 ADR의 선행 조건은 아님.
- Provider management UI/API와 LLM-backed detector capability: 별도 승인 전 비활성.

## Non-Goals

- Detector Provider 실제 SDK/endpoint 구현
- Provider 관리 UI/API와 credential 물리 schema 확정
- V1 Organization manager를 대체하는 별도 privacy reviewer RBAC role/permission 도입
- Knowledge ingestion 전체 리팩터링
- Source connector protocol 변경
- 탐지 결과로 security classification, permission, retention 또는 RBAC 자동 변경
- 기존 document의 파괴적 즉시 migration/reindex
- 탐지 정확도 100% 보장
