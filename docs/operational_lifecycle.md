# Operational Lifecycle, Retention And Async Ownership

Status: Draft
Verified Against: origin/dev @ deb7af7910ea0dc395cfb095f5b9f54aa8cfc709

## 목적과 문서 권한

이 문서는 여러 도메인에 흩어진 business lifecycle, retention, 비동기 작업의 소유권을 한눈에 비교하기 위한 system-level map이다. 개별 상태값, API shape와 DB 제약은 Accepted ADR 및 feature 문서가 우선하며, 테이블 상세는 [data_model.md](data_model.md)를 따른다. 이 문서는 특정 queue나 worker 구현을 PRD에 고정하기보다 다음 질문에 답한다.

- 사용자가 보는 business state의 source of truth는 무엇인가?
- 비동기 작업의 durable record와 commit owner는 누구인가?
- dispatch, 실행, retry, stale recovery와 dead-letter를 누가 소유하는가?
- terminal record와 민감 payload는 언제까지 보존하고 무엇을 먼저 지워야 하는가?

Linear issue 번호는 구현 추적용이며 설계 권한이 아니다. Current/Target 판정은 저장소 문서와 최신 dev 구현을 기준으로 한다.

## 상태 표기

| 상태 | 의미 |
| --- | --- |
| Current | 최신 dev에 physical record와 주 실행 경로가 존재한다. |
| Partial | 일부 record 또는 경로는 구현됐지만 recovery, retention, security invariant 중 하나 이상이 목표에 미달한다. |
| Pending Merge | 구현과 문서가 별도 브랜치에 존재하지만 최신 dev에는 아직 병합되지 않았다. Current inventory나 운영 전제로 사용하지 않는다. |
| Target | 승인된 방향이지만 구현 완료를 뜻하지 않는다. |
| Decision Required | 보존 기간, purge 조건, migration 방식처럼 별도 제품·보안·운영 결정이 필요하다. |

## 공통 불변식

1. Business resource 상태와 operational job 상태를 구분한다. Queue message, Celery result와 Redis notification은 business source of truth가 아니다.
2. 보안·권한·lifecycle mutation의 성공에 audit 또는 outbox가 필수라면 같은 DB transaction에서 함께 기록한다. 필수 record 저장 실패를 성공으로 반환하지 않는다.
3. 외부 I/O 중에는 DB row lock, transaction 또는 ORM session을 유지하지 않는다. Claim과 terminal 전이는 짧은 transaction으로 분리한다.
4. Retry 가능한 작업은 stable idempotency key, owner token/lease 또는 compare-and-set generation 중 해당 위험에 맞는 중복 방지를 가져야 한다.
5. Stale worker는 새 owner의 terminal state, active artifact 또는 cleanup 결과를 덮어쓰지 못한다. Fencing 또는 동등한 ownership 검증이 필요하다.
6. Operational record에는 raw secret, credential 원문, provider raw payload, source raw content와 사용자 입력 원문을 기본 저장하지 않는다. Retry에 payload가 필요하면 최소화·암호화·접근 통제·제한 보존을 별도로 정의한다.
7. `succeeded`, `failed`, `cancelled`, `dead_lettered` 같은 terminal 의미와 사용자-facing 상태를 구분한다. 내부 exact count나 target identity를 공개 projection으로 자동 노출하지 않는다.
8. Retention cleanup은 terminal 여부, legal hold, replay/reopen 가능성, 참조 무결성과 현재 owner 부재를 확인한 뒤 수행한다. Cleanup 자체도 idempotent하고 복구 가능해야 한다.
9. Linear 이슈나 미병합 브랜치의 구현·Accepted ADR은 최신 dev의 Current 계약을 선행 변경하지 않는다. 병합 뒤 physical schema, 운영 배포와 feature 문서를 함께 검증한 경우에만 Current로 전환한다.

## Lifecycle과 비동기 소유권 매트릭스

| 과정 | Business source of truth | Durable operational record | Producer와 commit 경계 | Executor와 recovery owner | 상태 |
| --- | --- | --- | --- | --- | --- |
| Generic audit 전달 | 각 business resource와 요청 결과, 최종 `audit_logs` | `audit_event_outbox` | Shared `record_audit()` producer. Caller session이 있으면 business transaction에 Outbox를 함께 넣고, 없는 legacy producer만 짧은 독립 transaction 사용 | Log System Beat/worker가 due·stale lease를 claim하고 같은 audit id를 멱등 저장. 최대 시도 뒤 dead-letter | Current |
| Transaction-bound canonical audit | 권한, membership, access-management, Security Alert lifecycle mutation과 `audit_logs` | 별도 Outbox가 아니라 같은 transaction의 `audit_logs` | 해당 application use case 또는 Unit of Work가 business change와 canonical audit를 함께 commit | Canonical audit 저장 자체는 비동기가 아니다. Notification·탐지 같은 후속 실패가 이미 commit된 보안 판단을 되돌리지 않음 | Current |
| Security Alert notification | `security_alerts`, evidence, lifecycle audit | `security_alert_notification_outbox` | Alert 생성·occurrence·lifecycle mutation과 notification Outbox를 같은 transaction에 기록 | Log System이 Redis 변경 신호를 at-least-once 전달하고 retry/dead-letter. Client는 payload를 상태로 쓰지 않고 영속 API를 재조회 | Current |
| Schedule dispatch | `schedules`, active deployment/App 상태 | `schedule_dispatch_claims` | Scheduler가 canonical schedule/deployment를 확인하고 occurrence claim을 DB에 확정 | Scheduler/Workflow worker가 deterministic admission과 claim state를 사용. Disabled 상태에서도 visibility/retention maintenance는 계속 수행 | Current |
| Public Conversation lifecycle | `conversation_sessions`, `conversation_access_grants`와 lifecycle revision | `conversation_secret_replays`, `conversation_purge_jobs`의 deployment/audience snapshot | Memory application use case가 Session·Grant mutation, replay record, purge request와 canonical audit를 같은 transaction에 확정한다. 기능은 명시적 activation과 독립 key·backup erasure 전제 없이는 비활성이다 | Memory가 만료 replay ciphertext의 bounded cleanup 정책과 repository를 소유하고 Log queue/worker는 주기 실행 host로만 사용한다. 전체 Session/Turn/Summary physical purge는 후속 범위다 | Pending Merge (MBA-317) |
| Workflow external effect | 고정된 workflow/deployment graph와 logical execution identity | `workflow_node_effect_attempts` | Workflow Engine application use case가 stable slot과 frozen provider contract를 짧은 transaction으로 claim·전이 | Claim owner만 provider를 호출한다. 같은 logical execution 재진입이 만료 attempt를 정리하며 별도 전역 recovery scheduler는 두지 않음 | Current |
| Knowledge Collection sync | `knowledge_collections`, membership과 child KB/document current state | `knowledge_collection_sync_jobs`, `knowledge_collection_sync_job_items` | Sync request service가 immutable topology revision과 bounded item snapshot을 durable job으로 확정 | Collection sync worker가 lease, fresh requester authority와 per-document advisory lock을 검증. Redelivery/stale lease는 DB job/item state로 복구 | Current |
| Knowledge document process/sync 실행 | `documents`, `document_versions`, active version과 retrieval-visible artifact | `knowledge_document_ingestion_jobs` (MBA-288) | MBA-288 브랜치 계약은 Document queued projection·설정과 job을 같은 transaction에 저장하고 commit 뒤 job UUID만 발행한다. 최신 dev의 process-local `BackgroundTasks`를 아직 대체하지 않음 | 전용 Knowledge worker가 fresh authority, lease/heartbeat/fencing, bounded retry와 due recovery를 소유하는 구현이 별도 브랜치에 있음 | Pending Merge |
| Knowledge privacy detection과 canonicalization | scoped Privacy Policy revisions, explicit Collection Privacy Policy Binding/Effective Snapshot, Provider/Detector Approval/Raw Parser Approval, global platform 및 per-Organization Artifact Validity Revision/epoch, redacted canonical content와 active artifact | Target Privacy Detection Attempt와 effective snapshot/digest 및 두 validity ref/epoch를 고정한 Privacy Decision Manifest (MBA-362) | Target admission은 actor/source/content safety, local parser 또는 approved raw-parser egress, all explicitly bound scoped policy/provider approvals, 두 current validity revision/epoch 및 fence를 snapshot한다 | MBA-362가 parser output -> local baseline -> exact provider -> local masking -> review when required -> canonical -> embedding/finalization을 소유한다. Routing membership은 privacy binding이 아니다. External parser/provider raw payload는 durable record가 아니며 security-invalidating binding/result change는 affected epoch의 `+1` CAS와 audit를 원자 확정한다 | Target / MBA-362 |
| Knowledge privacy manual review | Pending Privacy Review Candidate Revision, append-only Privacy Review Decision과 current policy/binding/validity/source authority | Encrypted staged candidate, generation-bound expiry/legal-hold/purge state, exact candidate-relative mask command, monotonic revised candidate와 mask/approve/reject decision | Organization manager가 provider-null mode도 처리하되 mask는 information-reducing range-only command이고 local baseline을 다시 실행한다. Decision, successor candidate 또는 generation review-state와 canonical review audit는 같은 Unit of Work며 successor는 최초 expiry를 연장하지 않는다 | Exact current non-expired candidate approval만 transaction 밖 embedding/finalization으로 진행한다. Approved body는 finalization commit 또는 TTL/authority invalidation 전까지 encrypted input으로 유지한다. Stale/blanket/expired approval, terminal block, source revoke 뒤 review/finalization request와 raw access 우회는 zero-write다. Generation-bound authority invalidation은 authoritative lifecycle transition으로 body를 `purge_pending` 처리하고 Knowledge cleanup reconciler가 24시간 안에 purge한다. Legal hold는 deletion만 보류한다 | Target / MBA-362 또는 review management 후속 |
| Knowledge privacy legacy migration | pre-cutoff legacy artifact와 target compliant active artifact | Target immutable Privacy Migration Inventory/Wave, frozen platform/Organization validity ref+epoch, eligibility retirement와 cleanup receipt (MBA-362) | Enforcement 전에 deployment-owned migration principal이 exact legacy set, snapshot revision, current validity refs/epochs, one-wave assignment, immutable activation time, `privacy_legacy_grace_v1`, DB-time deadlines와 audit를 원자 freeze한다. Legacy writer는 같은 epoch를 CAS한다 | Frozen/current validity epoch equality와 DB-time cutoff를 retrieval prefilter/final gate에서 강제해 invalidating epoch commit 즉시 legacy를 닫는다. Compliant active pointer swap은 eligibility retirement와 같은 transaction이고 cleanup receipt 뒤 rollback하지 않는다 | Target / MBA-362 |
| Knowledge protected raw copy cutover | Nodease-held upload/fetch raw copy와 opt-in/legal-hold policy | Exact Raw Copy Cutover Inventory, protected migration 또는 purge disposition/receipt | Deployment-owned cutover coordinator가 inventory를 freeze하고 valid opt-in과 retention/legal-hold 보존 조건을 모두 충족한 item만 encrypted protected store로 이관하며 migration 조건 미충족·no-hold item은 non-readable fence 뒤 purge한다 | Original-copy physical absence와 모든 terminal disposition을 확인한다. No-opt-in legal-hold conflict, unknown/partial/readable duplicate는 enforcement를 차단하고 legal hold만으로 opt-in을 만들거나 raw response 차단만으로 완료 처리하지 않는다. All-terminal readiness와 `knowledge.raw_copy_cutover.completed` audit은 원자 확정한다 | Target / MBA-362 |
| Knowledge active-version finalization과 superseded cleanup | `documents`, `document_versions`, active version과 version-scoped chunk | `knowledge_ingestion_outbox`는 현재 `cleanup_superseded` 물리 정리 intent만 소유하며 process/sync 요청 job이 아님 | Finalizer가 active pointer 전환, 이전 version `superseded`와 cleanup Outbox insert를 같은 transaction에 확정 | Log System task와 processor가 due/stale lease를 처리한다. 현재 handler는 superseded chunk cleanup에 한정되고 정기 schedule, 완전한 dead-letter/redrive, hard-delete/object/vector cleanup 연계는 MBA-184 범위 | Partial |
| Trace retention | `trace_payloads`와 visibility/redaction/retention policy | `trace_retention_policies` 및 cleanup 대상 row | Trace 저장 경계가 payload kind와 retention expiry를 정책에 따라 확정한다. System admin의 `/tracing/retention/purge`가 현재 명시적 producer이며 실제 purge는 Log queue에 위임한다 | `TraceRetentionService`는 payload를 expiry/생성 시각, run을 failed 여부별 age cutoff로 조회한다. Terminal/legal-hold 조건과 row lock이 없고 기본 `delete`에서 `WorkflowRun` 삭제 시 raw access event도 cascade 삭제된다 | Partial |
| Standalone RAG answer retention | `rag_answer_runs.retention_expires_at`와 safe summaries | 별도 answer run row가 lifecycle anchor | RAG answer service가 requested/running/terminal 상태와 expiry를 저장한다. `log.rag_answer_retention_purge` task는 있으나 Beat schedule과 Gateway producer는 없다 | 직접 task를 호출하면 `retention_expires_at`만으로 row를 조회한다. Terminal/legal-hold 조건과 row lock/marker가 없어 현재 운영 경로와 안전 불변식이 모두 미완성이다 | Partial |
| Cost Optimizer experiment retention | `cost_optimizer_experiments`의 compare lifecycle과 safe summary | `cost_optimizer_experiments`, `cost_optimizer_candidates` | Compare 생성 경로가 Trace metadata policy에서 `retention_expires_at`을 계산해 저장한다. Purge service는 있으나 등록된 task, Gateway producer와 Beat schedule은 없다 | 직접 service를 호출하면 expiry만으로 experiment를 조회한다. Terminal/legal-hold 조건과 row lock/marker가 없고 experiment 삭제는 candidate를 cascade 삭제하며 usage의 candidate FK만 `SET NULL`로 남긴다 | Partial |
| Knowledge Base hard delete | KB visibility/lifecycle와 direct permission, document/storage/index artifact | Current lifecycle facade. `knowledge_ingestion_outbox`의 현재 `cleanup_superseded` event만으로 hard-delete 전체를 보장하지 않음 | Current service는 permission/DB 정리와 best-effort physical cleanup을 조율하지만 완전한 durable cleanup transaction은 아님 | Target은 retry/dead-letter/redrive 가능한 cleanup Outbox/reconciler. MBA-184가 전환 추적 항목 | Partial |
| LLM provider usage 기록 | `provider_usage_operations`의 canonical outcome/token/cost와 correction revision | `provider_usage_operations`, `provider_usage_corrections`; `llm_usage_logs`는 compatibility projection | Capability path recorder가 final admission 뒤 intent와 provider-start fence를 각각 짧은 transaction으로 commit하고, 첫 post-call classification과 deterministic `llm.call` Audit Outbox를 같은 transaction에 기록한다. Provider I/O 중 DB session/lock을 유지하지 않는다 | Log System Beat가 60초마다 bounded `SKIP LOCKED` reconciliation을 실행한다. 15분 지난 `provider_started`는 provider 재호출 없이 `outcome_unknown`으로 닫고 pending/retryable projection은 2분 lease와 safe retry 상태로 수렴시킨다. 조사 완료 unknown은 platform IAM으로 제한한 Gateway image의 organization/CAS/no-replay 명령으로만 해소한다. Legacy-only usage와 succeeded ledger를 canonical mixed read로 합산한다 | Current (capability path), legacy recorder 병행 |
| LLM credential revoke와 secret purge | `llm_credentials.is_valid`와 credential permission/revision | Current revoke에는 별도 purge job이나 provider-call capability revision이 없음 | 현재 DELETE service가 `is_valid=false`를 동기 commit하며 row와 secret material은 유지 | 이후 credential resolver는 invalid row를 제외하지만 이미 조회·복호화해 만든 client는 provider 호출 직전 상태를 재검증하거나 fence하지 않는다. Concurrent revoke 뒤 outbound call race와 secret purge lifecycle이 남아 있다 | Partial revoke / Decision Required purge |

## Retention과 삭제 순서

| Record | 현재 보존 동작 | 삭제 전에 확인할 조건 | 보완 상태 |
| --- | --- | --- | --- |
| `audit_logs` | Canonical 감사 기록을 resource/run 삭제와 분리해 보존하고 nullable FK만 비운다 | 조직 정책, 규제/법적 보존, Security Alert evidence와 조사 요구 | 전역 purge 기간은 Decision Required |
| `audit_event_outbox` | 성공 payload는 `{}`로 비우고 idempotency tombstone을 남긴다. Retry/dead-letter payload는 복구를 위해 유지 | Terminal state, redrive 불필요, payload 최소화와 별도 audit 보존 | Terminal row 기간은 Decision Required |
| Trace payload와 `WorkflowRun` | System-admin 요청이 있는 경우 payload는 expiry/생성 시각, run은 failed 여부별 age cutoff로 bounded cleanup한다. 자동 Beat schedule은 없다 | 현재 서비스는 terminal/legal hold/active owner를 확인하거나 row lock을 잡지 않는다. Payload만 삭제하면 access event의 `payload_id`가 `SET NULL`로 남지만 `WorkflowRun` 삭제는 access event도 cascade 삭제한다 | Partial |
| `rag_answer_runs` | Row별 `retention_expires_at`와 purge task/service는 있으나 운영 producer/schedule이 없어 직접 task 호출에 한정된다 | 현재 서비스는 terminal state, legal hold, active owner와 동시 purge lock/marker를 확인하지 않는다. Target은 이 조건과 audit/usage 독립 보존을 함께 보장해야 한다 | Partial / manual-only |
| `cost_optimizer_experiments`와 candidate | Experiment별 `retention_expires_at`과 purge service는 있으나 task/API/schedule producer가 없어 직접 service 호출에 한정된다 | 현재 service는 terminal state, legal hold, active owner와 동시 purge lock/marker를 확인하지 않는다. Experiment 삭제는 candidate를 cascade 삭제하고 usage log의 candidate FK는 `SET NULL`로 분리 보존한다 | Partial / manual-only |
| Public capability replay | MBA-317은 짧은 replay window 동안 암호문만 저장하고 만료 row를 bounded batch로 주기 삭제한다 | 활성화 전에 live database cleanup과 별개로 backup에서 ciphertext 복구 불가를 보장하는 crypto-erasure 또는 database backup 미사용 운영 mode를 명시해야 한다 | Pending Merge (MBA-317) |
| Public Conversation Session/Turn/Summary | Privacy delete가 grant를 즉시 revoke하고 durable purge request/receipt를 만든다. MBA-317은 Session row가 사라진 뒤에도 receipt scope를 검증할 snapshot을 보존하지만 실제 content physical purge worker는 포함하지 않는다 | Purge fencing, terminal 실행, legal hold, audit·usage 독립 보존, backup erasure 완료와 receipt expiry | MBA-320 |
| KC sync job/item | Terminal 뒤 기본 30일 bounded cleanup | Active lease 없음, terminal 집계 완료, canonical audit 분리 보존 | Current |
| Knowledge document ingestion job | MBA-288 브랜치 계약은 terminal 뒤 기본 30일 bounded cleanup을 정의하지만 최신 dev에는 table과 운영 cleanup이 없음 | Terminal state, active lease/heartbeat 없음, finalization commit 완료, canonical audit/document version 독립 보존 | Pending Merge |
| Privacy Detection Attempt, Decision Manifest와 Artifact Validity Revision | MBA-362 Target. Raw bytes/text, parser/provider-safe view/map, exact span/fingerprint와 parser/provider response/exception은 attempt 종료·crash recovery에서 폐기하며 durable attempt/manifest에는 저장하지 않는다. Validity history는 `platform|organization` scope, monotonic epoch, preserving/invalidating transition과 effective time을 보존한다 | Safe attempt/manifest의 bounded retention, cleanup owner, terminal/fence 상태, legal hold, historical scoped policy/provider/parser approval revision tombstone과 `privacy_digest_hmac_sha256_v1` historical key retention/destruction 순서를 구현 전에 확정해야 한다. Manifest가 남아 있는 동안 effective snapshot과 두 validity ref/epoch의 역사·판정 가능성을 삭제하지 않는다 | Decision Required / MBA-362 |
| Privacy Review Candidate body와 Decision | MBA-362 Target. Candidate body는 encrypted protected staging에 두고 최초 생성 DB time부터 `privacy_review_candidate_ttl_v1 = 7 * 24 hours`를 적용한다. Successor는 expiry를 연장하지 않는다. Approve 뒤 body는 TTL 안의 finalization input으로만 남고 canonical finalization, expiry, reject, successor, generation authority revoke 또는 stale 전이 뒤 즉시 non-projectable이다. 개별 reviewer revoke는 candidate를 삭제하지 않는다 | Current cleanup claim/fence, DB-time expiry/terminal state, legal hold와 active owner 부재를 확인한다. Physical body는 purge-eligible 전이 후 24시간 안에 삭제하고 receipt/tombstone을 남기며 append-only Decision, safe audit, manifest provenance는 별도 보존한다. Legal hold는 deletion만 보류하고 review/approval을 재개하지 않는다 | Target / MBA-362 또는 review management 후속 |
| Privacy Migration Inventory/Wave와 cleanup receipt | MBA-362 Target. Enforcement 전 immutable pre-cutoff scope, frozen platform/Organization validity ref+epoch, activation time, `privacy_legacy_grace_v1`과 cutoff를 보존하고 cleanup receipt는 legacy artifact의 terminal 비가역 상태를 증명한다 | Runtime/preflight는 frozen/current epoch equality를 검사하고 invalidating epoch commit 즉시 legacy를 제외한다. 모든 inventory item이 compliant swap, 30-day hard max/cutoff-unavailable 또는 terminal exception으로 수렴하고 legal hold/cleanup이 끝나기 전에는 wave/receipt를 제거하지 않는다. Client extension이나 cleanup receipt 뒤 rollback은 허용하지 않는다 | Target / MBA-362 |
| Raw Copy Cutover Inventory/Disposition | MBA-362 Target. Existing Nodease raw copy별 `protected_migrated|purged` terminal disposition과 safe receipt를 보존한다 | Protected destination integrity 또는 purge, original-copy physical absence, legal hold와 duplicate copy 부재를 확인한다. 모든 item이 terminal이 아니면 enforcement를 활성화하지 않는다. Final readiness와 completion audit은 원자적이고 per-item receipt는 AuditLog로 복제하지 않으며 raw body/object key/content hash/item identity/exact count를 receipt/audit에 남기지 않는다 | Target / MBA-362 |
| External effect attempt | 현재 자동 cleanup 없음 | Broker duplicate-delivery 최대 기간, `replay_deadline_at`, reopen 불가, 결과 재사용 필요 종료를 모두 증명 | Decision Required |
| Knowledge cleanup outbox/artifact | 현재 `cleanup_superseded` processor는 있으나 terminal Outbox row의 자동 보존 만료와 전체 storage/index/hard-delete cleanup schedule은 완결되지 않음 | Retrieval exclusion 선행, active reference 없음, legal hold, cleanup terminal, stale worker 차단, redrive 불필요 | MBA-184 |
| Provider usage ledger/correction/projection | Credential revoke와 control resource 삭제와 무관하게 canonical ledger/correction을 유지한다. Compatibility projection의 nullable credential/model/workflow/run/candidate reference는 삭제 시 해제할 수 있다 | Billing/audit window, legal hold, unresolved outcome review, correction/reprojection 종료, aggregate 대체 가능성과 organization erasure 정책 | Canonical 보존 경계는 Current. 구체 보존 기간과 automatic physical purge는 Decision Required |
| LLM credential secret | Revoke 뒤에도 현재 row에 남고, 이후 resolver는 invalid row를 제외하지만 이미 materialize된 client에는 revoke fencing이 없다 | Provider-call 직전 capability/revision 재검증, grace/recovery 정책, legal hold, key/version과 audit, purge retry terminal | Partial revoke. MBA-248 및 별도 purge 결정 필요 |

## LLM usage와 credential 삭제 권고안

다음은 현재 구현 설명이 아니라 후속 결정을 위한 권고안이다. 구체 보존 일수나 physical column은 PRD에서 고정하지 않고 LLM Credentials·cost/usage feature와 migration에서 확정한다.

1. **Revoke와 purge를 분리한다.** 사용자 DELETE는 즉시 사용 중지(revoke)를 의미하고, 신규 option·capability·provider call을 차단한다. Secret erasure는 별도 보안 lifecycle로 둔다.
2. **Usage를 credential에 cascade delete하지 않는다.** Usage는 비용·감사 historical fact다. Credential/model 표시 row가 사라져도 당시 provider/model/pricing/billing 의미를 재구성할 최소 safe snapshot 또는 tombstone이 필요하다.
3. **Secret은 무기한 보존하지 않는다.** 짧은 recovery/grace와 legal hold를 평가한 뒤 encrypted envelope key 폐기(crypto-shred) 또는 검증 가능한 physical purge를 선호한다. 어느 방식이든 완료 audit와 idempotent retry가 필요하다.
4. **Raw usage와 aggregate retention을 분리한다.** 상세 usage는 조직별 비용 검증·분쟁·감사 window까지만 두고, 그 뒤 제품 요구에 충분하면 비식별 aggregate로 축약할 수 있다. 법적 보존과 organization deletion은 별도 우선순위를 갖는다.
5. **Compatibility FK와 canonical snapshot을 분리한다.** `llm_usage_logs.credential_id/model_id/workflow_id/workflow_run_id/cost_optimizer_candidate_id`는 nullable reference로 유지하고 삭제 시 연결만 해제한다. Capability path의 provider/model/pricing/principal 의미는 live control FK가 없는 `provider_usage_operations` safe snapshot이 보존한다. 필수 legacy user가 이미 없으면 projection을 terminal failure로 닫되 canonical 비용은 유지한다.
6. **Provider call 시작 뒤 revoke된 ambiguous outcome을 재호출하지 않는다.** 이미 발생한 usage reconciliation과 새 호출 권한을 분리하고, 같은 capability/attempt safe reference로 usage를 한 번만 정규화한다.

권고 이유는 보안 삭제를 비용·감사 이력 삭제와 묶으면 둘 중 하나를 희생하게 되기 때문이다. Revoke, secret erasure, usage retention을 세 lifecycle로 나누면 신규 호출 차단은 즉시 수행하면서도 각 보존 근거와 purge 완료를 독립적으로 검증할 수 있다.

## 변경 시 확인 목록

- 새 비동기 mutation을 추가할 때 business source of truth, operational record, commit owner, executor, recovery owner를 이 문서에 추가한다.
- Queue payload만 있고 durable admission/claim이 없다면 중복 전달과 publish-loss 허용 여부를 feature test에 명시한다.
- Retention 일수를 추가할 때 expiry 기준 시각, terminal 조건, legal hold, cascade/SET NULL, batch 상한과 failure state를 함께 정의한다.
- Retention 경로를 Current로 표시하기 전 task 정의뿐 아니라 실제 Gateway/Beat producer, terminal/legal-hold predicate, 동시 실행 제어와 FK cascade 영향을 함께 검증한다.
- Secret 또는 raw payload를 보존하는 새 job은 별도 encryption/key lifecycle, access permission과 safe dead-letter projection 없이 도입하지 않는다.
- Pending Merge 항목이 dev에 병합되면 physical table inventory, migration head, worker/Beat 배포, schema readiness, Gateway와 worker의 storage 접근 계약과 feature 문서까지 확인한 뒤 Current/Partial을 다시 판정한다. Redis progress는 durable job을 대체하지 않으며 terminal commit 뒤 정리되는 advisory projection으로만 검증한다.
- Current/Partial/Target 상태를 바꿀 때 관련 feature requirements, API, component, test case와 [data_model.md](data_model.md)를 함께 갱신한다.

## 관련 문서

- [Architecture](architecture.md)
- [Data Model](data_model.md)
- [Audit And Tracing](features/audit-tracing/requirements.md)
- [Knowledge](features/knowledge/requirements.md)
- [LLM Credentials](features/llm-credentials/requirements.md)
- [Cost Optimizer](features/cost-optimizer/requirements.md)
- [ADR-0029: Distributed Schedule Dispatch Claim](decisions/ADR-0029-distributed-schedule-dispatch-claim.md)
- [ADR-0035: External Effect Idempotency Boundary](decisions/ADR-0035-external-effect-idempotency-boundary.md)
- [ADR-0048: Knowledge Collection Sync Execution Boundary](decisions/ADR-0048-knowledge-collection-sync-execution-boundary.md)
