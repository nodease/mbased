# Audit Tracing Component Spec

Status: Draft
Verified Against: feature/mba-284 @ 5247ed1b

검증 값은 SafeChangeSummary와 audit actor management 연동, GenericAsyncAuditPublisher, Workflow/LLM 권한 변경 AuditRecorder 경계에 적용한다. 다른 trace UI는 각 feature 구현 기준을 따른다. 아래 KnowledgePrivacyObservabilityProjection은 ADR-0070 Target이며 현재 코드 검증 값에 포함되지 않는다.

## Screens

- Audit/Tracing은 MBA-188에서 별도 screen을 만들지 않는다.
- Organization audit list/detail과 actor management 진입 UI는 [Admin Dashboard component spec](../admin-dashboard/component_spec.md)의 `/dashboard/admin` AuditSearchTab, AuditDetailDrawer, ActorAccessDrawer가 소유한다.
- Security Alert 목록/detail/evidence와 lifecycle UI는 [Security Alert component spec](../security-alert/component_spec.md)의 Admin Dashboard `보안 알림` 탭이 소유한다. Audit/Tracing UI는 alert 상태를 별도로 계산하거나 저장하지 않는다.
- Organization member access semantics는 [Organization component spec](../organization/component_spec.md)이 소유한다.

## Components

### SafeChangeSummary

- Supported access-management event의 before/after safe field를 key/value 목록으로 표시한다.
- Unknown target/action 또는 empty summary면 렌더링하지 않는다.
- Raw JSON toggle이나 generic payload inspector를 제공하지 않는다.

### SafeDisplayReference

- Audit 목록과 상세에서 사람이 읽을 수 있는 label을 먼저 표시하고 canonical UUID를 보조 text와 복사 가능한 값으로 함께 표시한다.
- Actor는 감사 시점 snapshot label을 우선하고, target은 current organization의 allowlisted current label을 사용한다.
- Display projection이 없거나 대상이 삭제된 경우 오류를 표시하지 않고 기존 UUID만 표시한다.
- Allowlisted metadata/change summary의 UUID는 detail `resolved_references`에 일치하는 항목이 있을 때만 같은 방식으로 병기한다.

### AuditRecorder

- UI component가 아니라 backend outbound adapter contract다.
- Mutation use case와 같은 UnitOfWork에서 AuditLog row를 준비한다.
- Async best-effort publish 성공을 security mutation 성공 조건으로 사용하지 않는다.
- `/api/v1/permissions`의 Workflow/LLM credential team/user PUT·DELETE는 resource permission mutation application use case가 SQLAlchemy permission repository, transaction-bound audit adapter, UnitOfWork를 조율한다. Repository와 audit adapter는 commit/rollback을 호출하지 않는다.
- 동일 `auth_state` PUT은 기존 row를 반환하는 no-op으로 종료하고 canonical audit을 만들지 않는다. 적용된 create/update/delete만 safe organization/grantee/resource/auth-state snapshot으로 audit을 한 건 추가한 뒤 flush와 final commit을 수행한다.
- ORM delete는 tracked object를 deleted 상태로 만들기 전에 해당 object/operation의 manual audit ownership을 등록한다. 따라서 transaction-bound canonical audit와 after-commit listener 발행이 같은 permission 변경을 중복 소유하지 않는다.

### GenericAsyncAuditPublisher

- `record_audit()` 호출마다 audit event id를 먼저 고정하고 직렬화 payload를 한 번만 만든다.
- Caller SQLAlchemy session이 있으면 commit 없이 `audit_event_outbox` row를 같은 UnitOfWork에 추가한다. Session이 없는 legacy producer는 짧은 독립 session으로 Outbox를 commit한다.
- Rollout 4부터 `record_audit()`은 Outbox 저장만 수행하고 기존 `audit.record` Celery task를 발행하지 않는다. Outbox 저장 성공 시 audit id를 반환하고 실패 시 `None`을 반환한다.
- `audit.event_outbox.process`는 Beat가 30초마다 Log queue에서 실행한다. Due row를 `FOR UPDATE SKIP LOCKED`로 분배하고 lease/attempt를 먼저 commit한다.
- Provider usage ledger는 success·definitive failure·outcome unknown의 첫 durable classification과 deterministic `llm.call` Outbox insert를 같은 transaction에 묶는다. Provider usage reconciler는 같은 event id를 재발행하지 않고 operation state/projection만 조정하며 raw provider payload나 credential을 로드하지 않는다.
- Worker는 `AuditLog` insert, Outbox `succeeded` 전환, 성공 payload의 빈 JSON object 교체를 같은 transaction으로 commit한다. 성공 row는 idempotency key와 terminal 상태·시각만 tombstone으로 유지한다. 같은 audit id가 이미 있으면 멱등 성공이며, retry/dead-letter payload는 재처리를 위해 유지하고 실패는 safe reason code로 최대 5회 재시도한 뒤 dead-letter 처리한다.
- 성공 commit 뒤 Security Alert 탐지를 발행한다. 발행 실패는 저장 transaction을 되돌리지 않으며 기존 Security Alert reconciliation이 복구 경로다.
- 배포 전 broker에 들어간 메시지를 소진하기 위해 `audit.record` consumer는 호환성 task로 유지하지만 신규 producer에서는 사용하지 않는다.
- Outbox payload는 `workflow_run_id`/`workflow_node_run_id`를 top-level correlation으로 운반한다. 현재 producer의 기존 metadata 값도 호환 입력으로 승격하며 Outbox worker와 호환 consumer가 Run의 Workflow 조직과 `audit_metadata.organization_id`가 같은 경우에만 AuditLog typed FK 컬럼에 저장한다.
- Correlation migration은 정상 UUID, 실제 참조 row, Run의 Workflow 조직과 audit 조직의 일치가 모두 확인된 기존 metadata 값만 backfill한다. FK는 nullable `ON DELETE SET NULL`이며 두 컬럼에 개별 조회 인덱스를 둔다.

### KnowledgePrivacyObservabilityProjection (Target)

- Knowledge privacy runtime은 Audit/Tracing에 raw text, provider-safe view/map, exact span/confidence,
  source/canonical/span digest, endpoint, provider/credential identity 또는 provider exception을 넘기지
  않는다.
- Safe projection은 권한이 확인된 opaque attempt/manifest correlation, safe state/reason, 비민감
  contract revision, latency/count bucket과 retryability로 제한한다.
- Normal successful detection은 operational state/metric으로만 관측한다. Policy block은 canonical
  `policy.block`과 최상위
  `audit_metadata.policy_reason=knowledge.sensitive_content_detected`를 사용한다.
- Terminal blocked attempt의 safe state와 generic audit Outbox intent는 caller session의 같은
  Unit of Work에 추가하며 adapter는 commit하지 않는다. Preparation 실패에는 raw/direct-broker
  fallback이 없고 artifact/pointer를 남기지 않는다.
- Privacy Migration Coordinator는 immutable inventory, rollout marker와 canonical
  `knowledge.privacy_migration_wave.created` audit를 final exact-set freeze/enforcement epoch
  transaction에 추가한다. Non-authoritative staging에는 success audit를 만들지 않고, Audit에는
  safe Organization/server-issued opaque wave/deadline/cutoff/policy 및 frozen validity ref+epoch와 bounded count bucket만 투영한다. Internal
  `legacy_artifact_ref`, nullable-version migration fact와 exact chunk/index membership은 투영하지
  않는다.
- Raw Copy Cutover Coordinator는 Organization별 all-terminal disposition과 original-copy absence를 확인한
  final readiness marker와 `knowledge.raw_copy_cutover.completed` Audit Outbox intent를 한 Unit of Work에
  확정한다. Per-item receipt는 AuditLog로 복제하지 않고 safe Organization/opaque cutover ref와 bounded
  disposition count bucket만 투영한다. Audit 준비/commit 실패는 readiness와 함께 rollback한다.
- Effective scoped privacy policy/Collection privacy binding, provider/detector-approval/raw-parser management가 `artifact_invalidating` transition을 확정할
  때는 affected `platform|organization` current validity revision/monotonic epoch의 exact `+1` CAS와
  별도 canonical management audit를 같은 Unit of Work에 추가한다.
  이는 runtime `policy.block`이 아니며 exact action/actor/safe metadata가 승인되기 전에는 해당
  management adapter를 composition하지 않는다. Affected artifact/provider 목록과 security detail은
  audit projection에 넣지 않는다.
- Manual review mask/approve/reject application은 append-only decision, candidate successor 또는 generation
  review-state와 canonical review audit를 같은 Unit of Work에 추가한다. Safe Organization/document/candidate revision/outcome ref만 투영하고 candidate
  body, mask range, raw/span/digest는 넣지 않는다. Candidate expiry/terminal cleanup은 이 decision/audit/manifest를
  cascade 삭제하지 않고 safe purge receipt와 분리한다. Exact action contract 전에는 adapter를 composition하지 않는다.
- Legacy cleanup reconciler는 physical absence와 exact generation을 확인한 뒤 cleanup receipt,
  tombstone과 `knowledge.processing_artifact.purged`를 completion transaction에 exactly-once로
  추가한다. Safe Organization/wave/receipt/tombstone ref와 fixed reason만 투영하고 internal
  exact artifact ref/membership은 제외한다. Pre-delete `purging` fence/intent와 실패한
  completion은 success action이 아니며 visibility를 복원하지 않는다.
- TraceRedactionService는 이미 safe projection으로 들어온 telemetry를 방어적으로 정제하며
  Knowledge text normalization, detector 판단, masking 또는 finalization을 소유하지 않는다.

## States

- Change summary available: before/after를 구분해 표시한다.
- Change summary unavailable: 기존 audit detail만 표시하며 오류로 취급하지 않는다.
- Display unavailable: ID-only fallback을 표시하며 목록/detail 조회 실패로 취급하지 않는다.
- Historical/system/null actor: audit detail은 표시하고 actor management control은 제공하지 않는다.
- Sanitization failure: raw payload fallback 없이 summary를 생략하거나 safe error state를 반환한다.

## Interactions

- Audit row click은 AuditDetailDrawer를 연다.
- User actor button은 organization manager에게만 ActorAccessDrawer 진입을 제공한다.
- Actor access mutation 성공 후 audit list/detail을 다시 조회해 canonical event를 확인할 수 있다.
- Security Alert evidence row에서 audit detail로 이동하면 기존 AuditDetailDrawer의 권한과 allowlist를 다시 적용한다. Alert drawer는 raw audit metadata를 직접 렌더링하지 않는다.

## Accessibility

- Safe before/after는 색상만으로 구분하지 않고 `변경 전`, `변경 후` text label을 사용한다.
- Actor button, audit detail drawer, actor access drawer는 keyboard focus와 focus return을 제공한다.
