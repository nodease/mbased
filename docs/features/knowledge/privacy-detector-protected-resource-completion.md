# Knowledge Privacy Detector 보호 리소스 완결성 매트릭스

Status: Draft

이 문서는 [ADR-0070](../../decisions/ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)과
[보호 리소스 기능 완결성 체크리스트](../../engineering/protected-resource-feature-completion.md)를
Organization Detector Provider와 embedding 전 local masking 경계에 적용한다. MBA-333은 정책과
검증 가능한 계약을 확정하며, runtime·persistence·provider adapter 구현 완료를 주장하지 않는다.

## 대상

| 항목 | 범위 |
| --- | --- |
| 보호 리소스 | Organization/Collection/source/KB Privacy Policy Revision, Collection Privacy Policy Binding Revision, Effective Privacy Policy Snapshot, Privacy Review Candidate Revision/Decision, Detector Provider Registration/Revision, Detector Egress Approval Revision, Raw Parser Egress Approval Revision, Privacy Artifact Validity Revision, Privacy Detection Attempt, Privacy Decision Manifest, Privacy Migration Inventory/Wave, Raw Copy Cutover Inventory/Disposition, protected raw artifact |
| durable reference | all explicitly bound/applicable scoped policy와 Collection privacy/source/KB binding revision, compiled digest, pending candidate revision·generation-bound expiry·legal-hold/purge state와 append-only decision/receipt, provider/detector-approval/raw-parser revision, global platform 및 same-Organization validity revision ref/monotonic epoch snapshot, opaque attempt/manifest/wave ref, encrypted staged candidate/canonical ref, Organization-scoped keyed digest와 key version, Organization/KB/document/nullable-version의 exact opaque `legacy_artifact_ref`, wave의 frozen platform/Organization validity ref+epoch, legacy eligibility retirement, legacy snapshot revision와 enforcement epoch/time, raw-copy cutover terminal disposition/receipt |
| 실행 진입점 | Knowledge process/sync/resume/reindex admission, Knowledge worker, deployment preflight/create/enable, migration stage/freeze/cutoff, manual review와 policy/provider/parser management Target |
| 외부 I/O | source raw fetch, protected external parser raw upload, hard-baseline detector, Organization detector provider, embedding/index |
| 구현 책임 | MBA-362가 runtime, additive migration과 provider adapter를 구현한다. Provider/policy/review 관리·감사 surface는 MBA-362에 포함하거나 별도 이름 있는 후속 이슈로 닫아야 하며, 그 전에는 해당 mode를 비활성화한다. MBA-298 ingestion refactor는 관련 작업이지만 선행조건은 아니다. |

## 현재 안전 상태

- 현재 Knowledge ingestion에는 ADR-0070 순서의 authoritative privacy gate가 없으므로 기존
  artifact를 compliant하다고 표시하지 않는다.
- 현재 Organization detector provider 호출·등록·활성화 경로가 없으므로 미완성 provider가
  raw content를 외부로 전송할 수는 없다.
- Current `llamaparse`는 credential Organization/`use`를 확인하지만 protected raw parser egress
  approval/profile 경계가 아니므로 Target enforcement에서는 readiness 전 비활성화한다.
- Provider lifecycle, credential capability, revoke, exact cap/readiness, egress-approval과 canonical
  management audit가 구현되기 전에는 non-null provider path를 활성화하지 않는다. Review
  management/audit가 없으면 `manual_review_required`와 `manual_review`를 만들 수 있는 action policy도
  활성화하지 않는다. 명시적 Organization policy/validity bootstrap이 완료되고 review action을 만들지
  않는 범위에서만 `baseline_only`를 허용한다.
- Provider가 null인 manual review는 Target에서 허용하지만 review management/audit readiness 전에는
  비활성이다. 활성화 후에도 exact candidate의 range-only 추가 masking과 approval만 허용하고 terminal
  block, raw access 또는 미래 content를 포괄하는 승인을 우회하지 않는다.
- 현재 retrieval은 기존 raw-derived artifact를 계속 사용할 수 있지만 compliant 표시는 없다.
  Target enforcement에서는 새 generation 실패가 current-valid인 기존 compliant active-ready version을
  제거하거나 active pointer를 바꾸지 않는다. Security-invalidating policy/provider/approval transition은
  ready/pointer와 별개로 해당 manifest의 retrieval validity를 즉시 닫는다. 명시적 pre-cutoff inventory
  예외 밖이거나 cutoff 뒤인 legacy
  artifact는 retrieval fallback 또는 rollback-safe version이 아니며 compliant version이 없으면
  retrieval-unavailable이다.

## 경계 상태

`완료`는 실행 코드와 테스트 증거가 모두 있을 때만 사용한다. 이 PR의 문서 계약 완료는 동작 경계
완료와 구분한다.

| 경계 | 상태 | 계약 증거 | 현재 구현 | 검증 증거 | 해당 없음 사유 또는 후속 이슈 |
| --- | --- | --- | --- | --- | --- |
| 정책·식별자·Organization scope | 후속 이슈 | ADR-0070 Decision 1-4, Knowledge FR-153~FR-153v | scoped policy/Collection privacy binding/effective snapshot/provider/approval/validity revision과 attempt/manifest schema 없음 | 문서 정합성 검사만 수행 | MBA-362. Platform/Organization base와 모든 explicitly bound Collection/source/KB stricter revision을 strongest union으로 합성하고 exact Collection privacy/source/KB binding revisions와 digest를 snapshot한다. Routing membership은 privacy authority가 아니다. Missing/stale/복수 provider를 implicit baseline으로 처리하지 않으며 effective scope/result 변경은 Organization epoch을 invalidating `+1`한다. |
| 관리 API command/query와 UI | 후속 이슈 | ADR-0070 Decision 3-6, 10-11, Knowledge API/component spec | scoped policy/Collection privacy binding/provider/detector-approval/raw-parser/review route와 UI 없음 | 신규 route 부재를 정적으로 확인 | 별도 관리 이슈 또는 MBA-362 명시 범위. Actor, request schema, lifecycle, canonical audit action과 transaction ownership을 확정하기 전 non-null provider, external parser와 review-capable mode/action은 각각 필요한 관리 경계가 준비될 때까지 비활성이다. Collection privacy binding은 V1 Organization manager 전용 impact flow이고 `catalog_manage` membership과 분리한다. Manual review는 provider-null range-only masking과 exact approval을 지원하되 terminal block을 우회하지 않는다. Private profile/CIDR/transport는 Organization RBAC 밖의 deployment/change-control operator가 server-owned registry에서만 관리하고 Organization actor가 만들거나 확장하지 못한다. |
| 기존 process/sync/resume/reindex request schema | 후속 이슈 | Knowledge API spec의 server-owned selection 계약 | privacy provider/mode override를 받는 공식 필드 없음 | Target negative API test만 문서화 | MBA-362. Client가 endpoint, credential, mode, policy/provider revision을 주입하면 DB/network I/O 전 거부한다. |
| Deployment preflight | 후속 이슈 | ADR-0070 Decision 3, 9, Knowledge FR-153q/API/component spec | 현재 `KnowledgeDeploymentPreflightService`는 privacy manifest/validity/legacy-retirement/cutoff를 검사하지 않음 | Target preflight test만 문서화 | MBA-362. Standalone preview와 active create/enable/toggle은 runtime과 같은 retrieval-visible resolver를 사용한다. Stale/invalid manifest, frozen/current validity epoch 불일치 legacy, compliant pointer 뒤 legacy, expired/retired legacy와 artifact 부재는 `knowledge_privacy_artifact_unavailable` + `reprocess_or_remove_unavailable_knowledge`다. Invalidating epoch commit은 item projection/cleanup 전에도 즉시 차단한다. Preview는 `200 OK`와 safe blocked/허용된 inactive warning으로 투영하고 active mutation은 `409 deployment.preflight.blocked`로 차단한다. |
| Admission authorization과 source scope | 후속 이슈 | ADR-0070 Decision 3, 9 | current ingestion authorization은 존재하지만 privacy snapshot/fence와 결합되지 않음 | Target permission/race test만 문서화 | MBA-362. Active Organization, KB `write`, source-managed authority와 background actor를 raw fetch 전에 검증한다. |
| Content safety·local parser·protected raw parser egress 순서 | 후속 이슈 | ADR-0070 Decision 1, 2, 6, Knowledge FR-153p | current parser 뒤 authoritative baseline/provider gate가 없고 LlamaParse는 별도 raw-egress approval/profile을 사용하지 않음 | Target raw-upload/no-call test만 문서화 | MBA-362 또는 이름 있는 parser 후속. Local isolated parser가 기본이다. External parser는 Organization과 applicable source-managed source 또는 manual KB opt-in, exact Raw Parser Egress Approval Revision, server-owned `knowledge.parser.external_approved` profile/credential과 ADR-0067 public-address guarded transport 및 no-log/no-durable-payload readiness 전 upload 0회다. Private raw parser는 별도 Accepted ADR 전까지 지원하지 않고 output도 hard baseline을 우회하지 않는다. |
| Runtime/background privacy gate | 후속 이슈 | ADR-0070 Decision 1-9, 11 | `_extract_raw_blocks -> _build_document_chunks -> _persist_chunks_with_embeddings` 경로가 privacy gate를 통과하지 않음 | Target component/integration test만 문서화 | MBA-362. baseline -> exact provider -> local mask -> canonical -> chunk/embedding 순서를 구현한다. Legacy transition은 allowlist/wave 부재 시 unavailable이고 frozen/current platform·Organization validity epoch equality와 DB-time half-open cutoff를 retrieval prefilter/final evidence gate 모두에서 확인해 invalidating commit과 stale cache/vector result를 즉시 제외한다. |
| Provider port·egress·credential | 후속 이슈 | ADR-0070 Decision 4-6, ADR-0067 | provider port/adapter/egress path와 approval schema 없음 | Target contract/SSRF/network-isolation test만 문서화 | MBA-362. `external_approved`는 ADR-0067 public-address guard를 유지한다. `organization_private`는 generic private 예외가 아니라 exact server-owned host/port/CIDR allowlist, all-DNS-result 검증/address pinning/peer·Host·SNI, HTTPS+mTLS와 dedicated worker/network isolation을 가진 별도 profile을 요구한다. Profile/network readiness가 없으면 policy activation/call 0회다. |
| Text·span·masking contract | 후속 이슈 | ADR-0070 Decision 5, 7 | versioned privacy normalizer와 UTF-8 byte span validator 없음 | Target Unicode/fuzz test만 문서화 | MBA-362. `privacy_text_unicode_14_0_nfc_lf_v1`, half-open UTF-8 byte span, exact detector contract와 bounded rule/model refs, additive union과 fixed `[REDACTED]`를 구현한다. |
| Transaction·DB session·TOCTOU | 후속 이슈 | ADR-0070 Decision 3, 7, 9 | provider I/O와 privacy snapshot/finalization UoW 없음 | Target spy/PostgreSQL test만 문서화 | MBA-362. External I/O 중 session/transaction/row lock을 유지하지 않고 commit 직전 authority/snapshot/fence를 재검증한다. |
| Retry·idempotency·outcome unknown | 후속 이슈 | ADR-0070 Decision 9 | stable privacy attempt identity와 bounded retry 없음 | Target duplicate/partial/nondeterministic-result test만 문서화 | MBA-362. Partial response는 폐기한다. Current fence가 redacted candidate+manifest를 원자 staging commit한 뒤 retry는 provider를 다시 호출하지 않고 candidate를 재사용하며, 서로 다른 response를 merge하지 않는다. |
| Lease·claim·fencing·cancellation | 후속 이슈 | ADR-0070 Decision 9 | privacy-specific claim/fence 없음 | Target race/PostgreSQL test만 문서화 | MBA-362. Stale worker는 progress, outcome, cleanup, finalization을 commit하지 않는다. |
| Scoped policy/binding/provider/parser/review lifecycle와 revoke | 후속 이슈 | ADR-0070 Decision 4, 6, 8-11 | lifecycle management과 current artifact validity resolver 없음 | Target revoke/readiness/validity/candidate-expiry test만 문서화 | MBA-362 또는 별도 관리 이슈. Effective scoped policy, explicit Collection privacy binding과 review/provider/parser readiness를 활성화 전에 검증한다. Review readiness는 encrypted staging, code-owned 7-day TTL, terminal 즉시 비노출, fenced cleanup/reconciliation과 legal-hold 처리를 포함한다. In-flight call 뒤 revoke winner는 result/next batch/finalization을 차단한다. Effective scope/binding, detector/raw-parser security invalidation과 unknown transition은 Organization epoch을 `+1` CAS하고 audit와 원자 확정한다. Lifecycle surface가 없으면 해당 path를 조립하지 않는다. |
| Raw data·digest key·retention·purge | 후속 이슈 | ADR-0070 Decision 8, ADR-0014 | Current `documents.file_path`/original-content surface는 Target protected raw artifact 준수 증거가 아니며 raw-copy cutover disposition, privacy view/map/response/candidate cleanup owner와 Privacy Digest Key Ring도 없음 | Target canary/key-rotation/cutover/candidate-cleanup test만 문서화 | MBA-362. Current `/content` route를 dedicated raw/compliance flow로 재사용하지 않는다. Nodease-held upload/fetch 원문 exact inventory에서 valid opt-in과 retention/legal-hold 보존 조건을 모두 충족한 item만 protected raw artifact로 이관하고 migration 조건 미충족·no-hold item은 non-readable fence 뒤 물리 삭제해 original-copy absence와 terminal receipt를 증명해야 한다. No-opt-in legal-hold conflict는 activation을 차단하며 legal hold만으로 opt-in을 만들지 않는다. All-terminal readiness와 `knowledge.raw_copy_cutover.completed` audit은 원자 확정하고 audit 실패 시 활성화하지 않는다. Raw response 차단은 이 storage disposition의 대안이 아니다. 검출용 raw/view/map/response는 ephemeral이고 review candidate body는 encrypted staging, generation-bound 7-day TTL, terminal 즉시 비노출과 24-hour fenced purge를 요구한다. Safe attempt/manifest/decision/receipt는 body와 분리 보존한다. `privacy_digest_hmac_sha256_v1` key material은 DB/wire/관측 sink 밖에서 관리하고 historical key retention/destruction은 manifest retention/legal hold와 결합한다. |
| 오류·resource hiding·reason code | 후속 이슈 | ADR-0070 Decision 9, Knowledge API spec | 신규 privacy error producer 없음 | exact-string Target tests 문서화 | MBA-362. Scope hiding을 먼저 적용하고 privacy/provider identity를 오류로 노출하지 않는다. |
| Runtime block audit | 후속 이슈 | ADR-0070 Decision 10, ADR-0008 | 신규 privacy policy block producer 없음 | Audit/Tracing Target test 문서화 | MBA-362. Terminal attempt와 session-bound generic audit Outbox intent를 같은 Unit of Work에 `policy.block` + `audit_metadata.policy_reason=knowledge.sensitive_content_detected`로 기록하고 normal success는 high-cardinality AuditLog를 만들지 않는다. |
| Management/review canonical audit | 후속 이슈 | ADR-0070 Decision 10 | action registry와 transaction-bound recorder 미정 | 없음 | 별도 관리 이슈 또는 MBA-362 명시 범위. 각 경계의 exact actions와 rollback ownership을 ADR-0008/API/test에 확정하기 전 해당 non-null provider path 또는 review-capable mode/action을 독립적으로 활성화하지 않는다. |
| Trace·log·metric redaction | 후속 이슈 | ADR-0070 Decision 8, 10, Audit/Tracing spec | TraceRedactionService는 telemetry defense이며 Knowledge gate 증거가 아님 | Target sink canary test 문서화 | MBA-362. Raw/view/map/span/digest/provider identity·exception은 모든 durable sink에서 제외한다. |
| Migration·policy/validity bootstrap·legacy artifact | 후속 이슈 | ADR-0070 Decision 3, 9-12 | physical schema와 platform/Organization validity 및 Organization policy bootstrap 없음 | Target migration/rollout/validity/cutoff/no-fallback test만 문서화 | MBA-362. Enforcement-disabled 상태에서 all-writer dual-write 수렴과 구버전 writer drain/fence 뒤 existing Organization을 idempotent backfill하고 zero-missing rescan/readiness, no-default non-null DB foundation-generation/policy/validity constraint 뒤 활성화한다. Readiness를 우회한 old writer insert도 partial Organization 없이 rollback한다. Deployment-owned principal이 exact frozen legacy set, current platform/Organization validity ref+epoch와 enforcement epoch/cutoff를 확정한다. Runtime/preflight는 frozen/current epoch equality를 검사하고 invalidating epoch commit 즉시 legacy를 닫는다. Privacy-compliant active pointer swap은 같은 transaction에서 eligibility를 비가역적으로 retire하고, 이후 manifest stale/invalid 또는 cleanup 미완료에도 legacy로 복귀하지 않는다. Cleanup은 non-retrievable fence/intent 뒤 receipt/tombstone/`knowledge.processing_artifact.purged`로 완성한다. |
| Production quality/evaluation gate | 후속 이슈 | ADR-0070 Decision 11, Knowledge test spec | versioned detector corpus와 production gate 없음 | Target evaluation matrix 문서화 | MBA-362. 언어·문서 유형·category별 quality, review rate, latency/cost와 no-raw-egress canary를 검증한다. |
| 공식 정책·계약 문서 | 완료 | ADR-0070, architecture/data model, Knowledge requirements/API/component/test | Target 문서만 반영 | Markdown·링크·계약 정적 검사 | Runtime 완료 증거가 아니며 후속 구현 PR이 각 행을 갱신한다. |

## 후속 테스트 시나리오

| ID | 시나리오 | 기대 결과 | 테스트 계층 | 책임 |
| --- | --- | --- | --- | --- |
| PRIV-AUTH-01 | Cross-Organization policy/provider/review ref와 회수된 background actor로 실행한다. | Raw fetch와 provider/embedding 0회, safe hiding, zero write | API/application/PostgreSQL | MBA-362 |
| PRIV-SPAN-01 | 한글, combining mark, emoji와 provider offset variant를 포함한 text를 처리한다. | Exact NFC/LF와 UTF-8 half-open range, deterministic `[REDACTED]` output | shared core/property/adapter | MBA-362 |
| PRIV-EGRESS-01 | External provider request에 baseline canary와 endpoint/credential override를 주입한다. | Canary·raw fingerprint·내부 identity가 outbound/관측 sink에 없고 override/SSRF는 network 전에 차단 | adapter/fake server/security | MBA-362 |
| PRIV-EGRESS-02 | Private/external provider의 exact approval을 missing/stale/expired/revoked로 만들거나 private IP/mTLS만 제공한다. | Provider call 0회, approval/provider detail 비노출, finalization 전 revoke winner는 pointer swap 0회 | application/adapter/PostgreSQL | MBA-362 |
| PRIV-PARSER-01 | LlamaParse/external parser를 approval/profile/network readiness 없이 선택하거나 raw canary를 관측 sink에 주입한다. | Raw upload/provider/embedding 0회, `knowledge.raw_parser_egress_unavailable`, raw canary sink 0건 | application/adapter/security | MBA-362 또는 parser 후속 |
| PRIV-PREFLIGHT-01 | Stale manifest, frozen/current validity epoch 불일치 legacy, compliant pointer 뒤 남은 legacy, retired/expired legacy와 artifact 부재 KB를 deployment에 연결한다. | Preview는 `200` + safe blocked/허용된 inactive warning, active create/enable/toggle은 `409 deployment.preflight.blocked`, hidden identity/count 비노출 | Gateway/application | MBA-362 |
| PRIV-RACE-01 | Provider call 중 cancel, revoke, fence takeover를 각각 commit한다. | Loser가 다음 batch, embedding, finalization, cleanup을 commit하지 않고 기존 active version 유지 | worker/PostgreSQL | MBA-362 |
| PRIV-VALIDITY-01 | Preserving credential rotation, global baseline invalidation과 Organization policy/provider/approval invalidation을 active artifact, grace 중 legacy wave 및 stale cache/vector와 경합시킨다. | Preserving revision은 epoch 유지, global invalidation은 platform epoch `+1`, Organization invalidation은 해당 Organization epoch만 `+1`; revision/epoch CAS+audit commit부터 old manifest와 frozen legacy epoch가 즉시 unavailable이고 partial commit/cross-Organization 오염 없음 | application/retrieval/PostgreSQL | MBA-362 |
| PRIV-REDACT-01 | Raw/view/span/digest/parser/provider exception canary를 모든 응답·DB·job·audit·trace·log·metric sink에서 검색한다. | Safe opaque ref/state/reason/bucket 외 민감 값 0건 | integration/E2E | MBA-362 |
| PRIV-ROLLOUT-01 | Policy bootstrap 누락 Organization에서 non-null provider path 또는 review-capable mode/action을 필요한 management readiness 없이 활성화한다. | Missing policy와 각 path의 enable이 독립적으로 fail-closed하고 implicit downgrade가 없다 | migration/readiness/API | MBA-362 + 관리 후속 |
| PRIV-ROLLOUT-02 | Versioned/unversioned exact set, document wildcard/partial index, staging 뒤 set/validity mutation, legacy writer/freeze race, inventory 밖 등록, 30-day max 초과/client cutoff 연장, cutoff 동시 reindex, purge completion 실패와 cleanup receipt 뒤 rollback을 시도한다. | Exact `legacy_artifact_ref`, frozen current validity refs/epochs와 supported grace contract만 freeze, invalid/mutated/cross-Organization set과 unfrozen eligibility 0건, invalidating epoch commit 즉시 legacy 제외, pre-delete fence 뒤 visibility 복원 없음, receipt/tombstone/purge audit exactly-once, 등록/상한 초과/연장/rollback zero-write, cutoff 뒤 non-compliant document unavailable | migration/PostgreSQL/E2E | MBA-362 |
| PRIV-REVIEW-01 | Provider-null manual mode에서 valid/stale/terminal/TTL equality candidate에 range-only mask, blanket approval과 exact approval을 시도한다. | Valid mask는 최초 expiry를 유지한 새 pending revision과 baseline 재검사, exact non-expired approval만 finalization. Replacement/raw body, stale/blanket/terminal/expired approval은 zero-write | application/API/PostgreSQL | MBA-362 또는 review 후속 |
| PRIV-REVIEW-02 | Approve-finalize 재시도, successor mask 반복, reject/finalize/generation-bound source authority revoke/stale generation, 개별 reviewer revoke, cleanup worker takeover와 legal hold를 7-day TTL/24-hour purge 경계에서 경합시킨다. | Approve 뒤 body는 finalization commit/TTL까지 보존, successor TTL 미연장, reviewer revoke는 actor 요청만 차단, generation authority revoke/terminal은 즉시 비노출·non-mutable, current fence owner만 body purge+receipt, legal hold는 deletion만 보류하고 Decision/audit/manifest는 보존 | application/storage/PostgreSQL | MBA-362 또는 review 후속 |
| PRIV-RAW-CUTOVER-01 | Opt-in/비-opt-in/legal-hold/unknown item, migration 뒤 duplicate old copy, purge 뒤 absence 실패와 raw-response-only 차단을 구성한다. | 모든 exact item이 `protected_migrated|purged`와 original-copy absence로 수렴할 때만 activation. Unknown/partial/duplicate/hold conflict와 response 차단만 있는 경우 activation 0건, raw/object key 비노출 | migration/storage/PostgreSQL | MBA-362 |
| PRIV-BIND-01 | `catalog_manage` membership link/reorder/unlink, `lifecycle_manage` archive/restore와 Organization manager privacy bind/unbind를 경합시킨다. | Delegated routing/lifecycle mutation은 privacy/epoch 불변, archived binding은 계속 effective, active binding unlink/hard-delete/policy purge는 safe conflict, exact manager binding winner만 epoch+audit commit | application/API/PostgreSQL | MBA-362 또는 management 후속 |
| PRIV-BOOTSTRAP-01 | Initial scan 사이 구버전 Organization create, new-writer create/backfill 경합, old-image rollback과 readiness 우회 insert를 시도한다. | Old writer fence 전 activation 없음, final zero-missing 수렴, duplicate bootstrap 없음, no-default DB foundation constraint 확정, enforcement 뒤 old image readiness 실패와 우회 insert 전체 rollback | migration/readiness/PostgreSQL | MBA-362 |

## 갱신 규칙

- 후속 구현 PR은 자신이 소유한 행에 코드 위치와 실행 가능한 테스트 증거를 연결한다.
- 문서 계약만 존재하는 행을 `완료`로 바꾸지 않는다.
- Provider/raw 외부 I/O 전 fail-closed, 권한 회수, secret·PII 비노출, stale worker 차단,
  current-valid active artifact만 보존 중 하나라도 미완료면 non-null provider path를 활성화하지 않는다.
- Physical table, retention 기간, canonical audit action을 확정하면 ADR/API/data model/test를 같은
  변경에서 갱신한다.
