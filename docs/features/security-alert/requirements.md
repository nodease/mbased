# Security Alert Requirements

Status: Draft
Related Features: audit-tracing, admin-dashboard, organization, auth, workflow, knowledge, llm-credentials

## Purpose

Security Alert는 검증된 organization 안에서 인증 사용자가 짧은 시간에 권한 거부나 정책 차단을 반복하는 패턴을 탐지하고, 현재 organization owner/manager가 이를 확인·조사·해결할 수 있게 한다.

이 기능의 source of truth는 개별 차단 사건을 저장하는 `audit_logs`와 탐지 결과 및 대응 상태를 저장하는 Security Alert다. Alert는 실제 침해를 확정하지 않고 관리자가 조사해야 할 위험 신호를 나타낸다.

탐지와 lifecycle 정책은 [ADR-0028](../../decisions/ADR-0028-security-alert-detection-and-lifecycle.md), late-arrival reconciliation은 [ADR-0042](../../decisions/ADR-0042-security-alert-reconciliation-receipts.md)를 따른다. Canonical audit action naming은 [ADR-0008](../../decisions/ADR-0008-audit-action-naming-standard.md), organization scope 밖 resource hiding은 [ADR-0010](../../decisions/ADR-0010-resource-access-403-404-policy.md)을 따른다.

## User Stories

- Organization owner/manager로서, 같은 사용자가 권한 없는 행동을 반복하면 직접 audit를 계속 검색하지 않고 Security Alert로 확인하고 싶다.
- Organization owner/manager로서, 한 사용자가 여러 resource에 접근을 시도하는 패턴을 발견하고 관련 audit 근거를 확인하고 싶다.
- Organization owner/manager로서, 정책 차단이 반복되면 원인별로 묶인 alert를 확인하고 조사·해결 상태를 남기고 싶다.
- 감사 담당자로서, alert 생성과 관리자 대응이 canonical audit로 추적되고 민감한 원문이 복사되지 않기를 원한다.

## Eligible Audit Events

- SAL-REQ-001: Security Alert detector는 `actor_id`가 존재하고 `actor_type='user'`, `category='action'`, `status='failure'`인 audit만 입력 후보로 사용해야 한다.
- SAL-REQ-002: 입력 후보는 `audit_metadata.organization_id`에 audit 생성 시점에 검증된 organization UUID를 포함해야 한다. Detector는 target resource를 다시 조회해 organization을 추론하지 않아야 한다.
- SAL-REQ-003: `permission.denied` 입력은 scope 확인이 끝난 `target_type`과 opaque `target_id`를 포함해야 한다. Target이 없거나 안전성이 확인되지 않은 event는 다중 resource 탐지에서 제외해야 한다.
- SAL-REQ-004: `policy.block` 입력은 최상위 `audit_metadata.policy_reason`에 `{domain}.{reason}` 형식의 canonical reason을 포함해야 한다.
- SAL-REQ-005: Canonical policy reason은 `^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$` 형식을 따라야 한다. 신규 producer는 legacy reason만 기록해서는 안 된다.
- SAL-REQ-006: Legacy `pii_policy_blocked`는 읽기와 reconciliation에서 `rag.pii_evidence_detected`로 정규화해야 한다. Append-only인 기존 audit row를 수정하거나 backfill하지 않아야 한다.
- SAL-REQ-007: Security Alert policy allowlist는 `access_management.*`, `rag.pii_evidence_detected`와 `knowledge.sensitive_content_detected`를 포함해야 한다. `budget.exceeded`, 형식이 잘못된 reason, allowlist에 없는 reason은 보안 alert 입력에서 제외해야 한다.
- `knowledge.sensitive_content_detected` producer와 allowlist 활성화는 ADR-0070 Target이며 MBA-362가 구현한다. 이 문서 변경만으로 현재 ingestion이 privacy block audit를 생성한다고 간주하지 않는다.
- SAL-REQ-008: `user.login_failed`, organization scope가 없는 `auth.permission_denied`, scope 밖 resource 404, validation 실패, desired-state no-op은 입력에서 제외해야 한다.
- SAL-REQ-009: Detector는 기능 활성화 시점 이후 발생한 audit만 처리해야 한다. 활성화 이전 audit은 실시간 처리와 reconciliation 모두에서 backfill하지 않아야 한다.

## Detection Rules

| Rule ID | Detection key | Window and threshold | Severity |
| --- | --- | --- | --- |
| `repeated_permission_denied` | organization + actor + rule + version | 5분 안에 `permission.denied` 5회 | `medium` |
| `multi_resource_permission_probe` | organization + actor + rule + version | 10분 안에 서로 다른 안전한 target 5개에서 `permission.denied` | `high` |
| `repeated_policy_block` | organization + actor + rule + version + policy reason | 10분 안에 같은 `policy_reason`의 `policy.block` 3회 | `high` |

- SAL-REQ-010: 초기 detection rule version은 `v1`이어야 한다. Threshold나 detection key 의미가 바뀌면 기존 alert와 섞이지 않도록 version을 올려야 한다.
- SAL-REQ-011: 다른 organization, actor, rule version은 같은 threshold에 합산하지 않아야 한다. `repeated_policy_block`은 서로 다른 policy reason도 합산하지 않아야 한다.
- SAL-REQ-012: 동일 audit은 여러 rule의 조건을 각각 만족할 수 있지만 같은 rule에서 두 번 계산되면 안 된다.
- SAL-REQ-013: 다중 resource 규칙의 distinct target은 `(target_type, target_id)` 조합이어야 한다. 같은 target 반복은 하나로 계산해야 한다.
- SAL-REQ-014: 탐지 window는 `audit_logs.occurred_at` UTC를 기준으로 계산하고 `window_start <= occurred_at <= current_event.occurred_at` 범위의 양 끝을 포함해야 한다.
- SAL-REQ-015: Audit 1건은 occurrence 1회로 계산하고 `audit_log.id`를 idempotency key로 사용해야 한다.
- SAL-REQ-016: Threshold 직전에는 alert를 만들지 않고 threshold에 도달한 event에서 alert를 만들어야 한다.

## Alert Aggregation And Cooldown

- SAL-REQ-017: 같은 detection key에는 `open` 또는 `acknowledged` 상태의 활성 alert가 최대 하나만 존재해야 한다.
- SAL-REQ-018: 마지막 탐지 시각부터 30분의 sliding cooldown을 적용해야 한다. Cooldown이 끝난 활성 alert가 새 audit만으로 같은 rule threshold를 다시 충족하면 새 alert를 만들지 않고 새 episode로 기록해 관리자 notification refresh를 다시 발생시켜야 한다.
- SAL-REQ-019: Cooldown 중 같은 detection key의 event는 검증된 audit organization이 alert organization과 일치할 때만 기존 활성 alert의 occurrence count와 `last_detected_at`을 갱신하고 evidence로 연결해야 한다. ID만 전달된 audit도 canonical row를 조회해 같은 organization 검증을 적용해야 한다.
- SAL-REQ-020: Cooldown 중 occurrence 갱신은 별도 canonical audit action을 만들지 않아야 한다.
- SAL-REQ-021: `resolved` alert에는 새 evidence를 연결하지 않아야 한다.
- SAL-REQ-022: Resolve 이후 발생한 새 audit만으로 threshold를 다시 충족했을 때 새 alert를 만들어야 한다. 이전 alert에 연결된 audit을 새 threshold 계산에 재사용하지 않아야 한다.

## Lifecycle And Response

- SAL-REQ-023: Alert status는 `open`, `acknowledged`, `resolved`만 허용해야 한다.
- SAL-REQ-024: 허용 상태 전이는 `open → acknowledged`, `open → resolved`, `acknowledged → resolved`, `acknowledged → open`이어야 한다. 그 밖의 전이는 거부해야 한다.
- SAL-REQ-025: Acknowledge 전이 시점에는 인증·인가된 처리 관리자와 처리 시각을 필수로 기록해야 한다. 이후 처리 관리자 user가 삭제되면 actor FK는 `NULL`이 될 수 있지만 처리 시각과 lifecycle 이력은 보존해야 한다.
- SAL-REQ-026: Resolve 전이 시점에는 인증·인가된 처리 관리자, 처리 시각, resolution type, sanitized reason을 필수로 기록해야 한다. 이후 처리 관리자 user가 삭제되면 actor FK는 `NULL`이 될 수 있지만 처리 시각, resolution 정보, canonical audit은 보존해야 한다.
- SAL-REQ-027: Resolution type은 `mitigated`, `false_positive`, `accepted_risk`만 허용해야 한다.
- SAL-REQ-028: Resolve reason은 필수이며 organization management reason과 같은 정규화, 길이 제한, control-character 차단, secret/PII redaction 기준을 적용해야 한다.
- SAL-REQ-029: 해결된 alert는 삭제하지 않아야 한다. Alert delete API와 자동 retention 삭제는 이 범위에서 제공하지 않아야 한다.
- SAL-REQ-030: 별도 read/unread 상태를 만들지 않아야 한다. Sidebar badge는 `open` alert만 계산하고 `acknowledged`, `resolved`는 제외해야 한다.

## Canonical Alert Audit

| Event | Canonical action | Actor |
| --- | --- | --- |
| Alert 최초 생성 | `security_alert.detected` | `system` |
| 관리자 확인 | `security_alert.acknowledged` | 처리 관리자 |
| 조사 재개 | `security_alert.reopened` | 처리 관리자 |
| 해결 | `security_alert.resolved` | 처리 관리자 |

- SAL-REQ-031: Alert lifecycle audit은 `category='action'`, `target_type='security_alert'`, `target_id=<security_alert_id>`를 사용해야 한다.
- SAL-REQ-032: 상태 변경 성공 audit은 `status='success'`를 사용해야 한다.
- SAL-REQ-033: Lifecycle audit metadata는 검증된 organization ID, rule ID/version, severity, sanitized resolution type/reason만 허용해야 한다.
- SAL-REQ-034: Alert 최초 생성, 최초 evidence 연결, `security_alert.detected` audit은 같은 DB transaction에 기록해야 한다. 하나라도 실패하면 모두 rollback해야 한다.
- SAL-REQ-035: Cooldown occurrence 갱신은 `security_alert.detected`를 다시 기록하지 않아야 한다.

## Processing And Recovery

- SAL-REQ-036: Audit 저장 성공 이후 별도 비동기 task가 실시간 탐지를 수행해야 한다. 탐지는 원래 authorization 판단이나 사용자 응답을 지연하거나 변경해서는 안 된다.
- SAL-REQ-037: 탐지 실패 시 원본 audit을 삭제하거나 authorization 결과를 바꾸지 않고 탐지 작업만 재시도해야 한다.
- SAL-REQ-038: Reconciliation은 실시간 task와 같은 eligible-event 정규화와 rule 평가 함수를 사용해야 한다.
- SAL-REQ-039: Reconciliation은 PostgreSQL watermark의 기능 활성화 시각·batch generation과 processor별 audit receipt의 발견·최종 평가 generation을 durable하게 저장해야 한다. 활성화 이후 receipt가 없는 audit는 event-time cursor보다 과거에 늦게 commit돼도 처리 대상이어야 한다. 늦은 eligible audit가 기존 판단을 바꿀 수 있으므로 같은 organization·actor·action 범위에서 해당 audit부터 최대 rule window 안의 receipt 보유 후속 audit도 다시 평가하되 다른 organization·actor·action 범위와 window 밖 audit는 재평가하지 않아야 한다. 한 실행은 `(occurred_at, audit_log.id)` 순서로 최대 100건만 처리하고 성공한 batch의 receipt·generation·event-time cursor와 Alert/evidence·notification Outbox 변경만 같은 transaction에서 commit해야 한다. 실패한 batch는 모두 rollback하고 다음 retry 또는 1분 주기 실행이 같은 backlog부터 이어서 처리해야 한다. Rule window 평가는 계속 `occurred_at`을 사용해야 한다.
- SAL-REQ-040: 실시간 task, retry, reconciliation이 같은 audit을 동시에 처리해도 evidence, occurrence, 활성 alert가 중복 생성되지 않아야 한다. 서로 다른 audit을 같은 활성 alert에 동시에 연결해도 occurrence를 유실하지 않고 `last_detected_at`은 가장 최신 event time을 유지해야 한다.
- SAL-REQ-041: Alert 생성 또는 활성 alert 갱신 commit 이후 notification 변경 신호를 발행해야 한다.
- SAL-REQ-042: Notification 발행 요청은 Alert 생성·occurrence/episode 갱신·lifecycle 변경과 같은 DB transaction의 durable Outbox에 기록해야 한다. Redis 발행 또는 현재 manager 수신자 조회 실패는 Alert transaction을 rollback하지 않고 최대 5회 재시도한 뒤 dead-letter로 보존해야 한다.
- SAL-REQ-043: Eligible event 발생 후 관리자 UI 반영 목표는 1분 이내여야 한다.

## Authorization And Organization Isolation

- SAL-REQ-044: 현재 active organization의 owner/manager만 해당 organization Security Alert를 조회하고 상태를 변경할 수 있어야 한다.
- SAL-REQ-045: Alert 생성 당시 manager였다는 사실을 현재 접근 권한으로 사용하지 않아야 한다.
- SAL-REQ-046: Membership 정지·제거 또는 manager 강등은 이후 alert 조회와 상태 변경을 즉시 차단해야 한다.
- SAL-REQ-047: 일반 member와 audit 조회 전용 auditor/raw auditor는 Security Alert를 조회하거나 변경할 수 없어야 한다.
- SAL-REQ-048: 다른 organization 또는 scope 밖 alert ID는 `404 resource.not_found`로 숨겨야 한다.
- SAL-REQ-049: Client의 권한별 UI 차단은 UX 보조로만 사용하고 Gateway가 모든 조회와 상태 변경에서 권한을 다시 검증해야 한다.

## Admin And Notification Experience

- SAL-REQ-050: Admin Dashboard는 severity, status, rule, actor, 기간으로 Security Alert를 검색할 수 있어야 한다.
- SAL-REQ-051: Alert detail은 occurrence count, 최초·최근 탐지 시각, rule/severity/status, 안전한 관련 audit 근거를 표시해야 한다.
- SAL-REQ-052: Organization owner/manager는 detail에서 acknowledge, resolve, reopen을 수행할 수 있어야 한다.
- SAL-REQ-053: UI는 alert를 실제 침해가 확정된 사건이 아니라 탐지된 위험 신호로 표현해야 한다.
- SAL-REQ-054: Sidebar는 `open` alert 개수 badge와 최근 safe summary를 표시하고 Admin Dashboard Security Alert 화면으로 이동할 수 있어야 한다.
- SAL-REQ-055: Security Alert notification source of truth는 invitation notification과 분리해야 한다. 기존 organization invitation 조회·수락·거절 동작을 변경해서는 안 된다.
- SAL-REQ-056: SSE event는 alert 전체 내용이나 evidence를 전달하지 않고 재조회 신호와 최소 safe summary만 전달해야 한다.
- SAL-REQ-057: SSE reconnect 또는 event 누락 이후 영속 alert 목록 재조회로 현재 상태를 복구할 수 있어야 한다.
- SAL-REQ-058: Active organization 전환과 manager 권한 회수는 Sidebar, 목록, SSE scope에 즉시 반영되어야 한다.
- SAL-REQ-059: 반복 occurrence가 관리자 toast를 무제한 생성하지 않도록 client notification cooldown을 적용해야 한다.

## Data Protection

- SAL-REQ-060: Alert, evidence projection, notification, SSE, metric, log에 raw email, IP, user-agent, exception, request body, token, credential, trace/document payload를 저장하거나 반환하지 않아야 한다.
- SAL-REQ-061: Hidden resource의 이름, 경로, 원문 metadata, 존재 여부를 alert에서 복원하거나 노출하지 않아야 한다.
- SAL-REQ-062: Actor 이름과 이메일 snapshot을 Security Alert row에 복사하지 않아야 한다. 표시가 필요하면 현재 organization 권한 경계 안에서 safe user projection을 조회해야 한다.
- SAL-REQ-063: Actor가 삭제됐거나 표시할 수 없으면 opaque actor ID 또는 삭제된 사용자 상태로 표시해야 한다.
- SAL-REQ-064: Related audit는 raw `audit_metadata`, `before`, `after`를 그대로 반환하지 않고 audit-tracing allowlist를 따른 safe projection만 제공해야 한다.
- SAL-REQ-065: Security Alert 관리자 API의 권한 거부 기록은 관리자가 원인을 이해할 수 있도록 고정 allowlist의 필요 권한, 시도한 작업, 거부 사유를 제공해야 한다. Raw URL/path/query, header, request body, exception은 기록하거나 반환하지 않아야 한다.
- SAL-REQ-066: `notifications.changed`는 Alert 생성, 새 evidence에 의한 occurrence 갱신 또는 lifecycle 상태 변경이 실제로 commit된 경우에만 발행해야 한다. Threshold 전 event처럼 Alert가 변경되지 않은 경우에는 발행하지 않아야 한다.
- SAL-REQ-067: Security Alert notification 수신자는 현재 관리자 API 권한 판정과 같은 집합이어야 한다. Active manager membership뿐 아니라 membership이 없는 유효한 `Organization.created_by`/`managed_by`를 포함하고 suspended, removed, deactivated 사용자는 제외해야 한다.
- SAL-REQ-068: 열린 detail에서 acknowledge, reopen, resolve가 현재 권한 회수로 `403`을 반환하면 Client는 cached detail과 해결 dialog를 비우고 선택된 `alertId` URL을 닫아야 한다.
- SAL-REQ-069: Rule ID/version, action, window, threshold, severity, count mode와 policy-reason grouping은 하나의 server-owned rule registry를 source of truth로 사용해야 한다. Evaluator, aggregation threshold 확인과 worker 최대 조회 window는 별도 상수를 중복 정의하지 않고 같은 registry를 읽어야 한다.
- SAL-REQ-070: 운영 반영 전 rule replay는 지정한 organization과 기간의 audit를 읽기 전용으로 평가하고 rule별 발화 횟수의 safe aggregate만 반환해야 한다. Replay는 lookback 구간과 실제 평가 구간을 각각 완전하게 읽은 경우에만 집계를 반환해야 하며, 어느 구간이든 설정된 limit을 초과하면 부분 결과를 성공으로 반환하지 않고 safe reason code로 실패해야 한다. Replay는 Security Alert, evidence, lifecycle audit, notification, watermark를 생성·변경하거나 raw audit payload와 target을 출력하지 않아야 한다.
- SAL-REQ-071: 최초 alert는 `episode_count=1`과 `last_episode_started_at=first_detected_at`으로 시작해야 한다. Cooldown 종료 후 threshold 재충족 시 새 evidence가 실제 연결된 transaction만 episode count를 한 번 증가시키고 마지막 episode 시작 시각을 threshold event time으로 갱신해야 한다. 이 시각은 notification 전달 성공 시각을 의미하지 않는다.
- SAL-REQ-072: SSE 또는 notification 신호에 따른 같은 organization·filter·page·alert의 background refresh는 현재 목록, 상세와 evidence를 응답 전까지 유지하고 성공 응답으로 한 번에 교체해야 한다. Organization, filter, page 또는 alert scope가 바뀌거나 현재 권한이 회수된 경우에는 이전 scope 데이터를 유지하면 안 된다.

## Non-Functional Requirements

- SAL-NFR-001: Rule evaluation, evidence 연결, lifecycle mutation은 retry와 concurrency에서 deterministic하고 idempotent해야 한다.
- SAL-NFR-002: Alert 목록과 Sidebar count는 organization, status, severity, rule, actor, 탐지 시각 기준 조회가 가능한 index 전략을 가져야 한다.
- SAL-NFR-003: Rule ID, policy reason, organization ID, actor ID처럼 bounded 또는 opaque한 값만 metric label 후보로 사용해야 한다. Raw target ID나 사용자 입력 reason을 고-cardinality metric label로 사용하면 안 된다.
- SAL-NFR-004: Alert 처리 장애는 safe structured log와 metric으로 관찰할 수 있어야 하며 raw audit payload나 exception 원문을 durable log에 남기면 안 된다.
- SAL-NFR-005: Security Alert 도입으로 기존 workflow 생성·저장·실행·배포, RAG 실행, audit 검색, organization invitation notification 흐름이 깨지지 않아야 한다.

## Out Of Scope

- 인증 전 로그인 실패와 비로그인 `auth.permission_denied` 탐지
- IP/account fingerprint와 credential-stuffing 탐지
- 플랫폼 운영자 경보
- 비용 급증, 실행 실패, 대량 삭제 등 운영 이상
- 이메일, Slack, SIEM 등 외부 전달
- Organization별 rule/threshold 설정 UI
- ML 기반 이상 탐지
- 자동 계정 정지 또는 자동 권한 회수
- 기능 활성화 이전 audit backfill
- Alert retention과 자동 삭제
- Severity 자동 상승

## Delivery Dependencies

| Issue | Scope | Implementation Status |
| --- | --- | --- |
| MBA-223 | 탐지 대상 audit organization/reason 정규화 | 구현됨 |
| MBA-211 | Alert/evidence 영속 모델과 lifecycle | 구현됨 |
| MBA-212 | 실시간 탐지와 reconciliation | 구현됨 |
| MBA-213 | 관리자 조회·상태 변경 API | 구현됨 |
| MBA-214 | Admin Dashboard, Sidebar, SSE | 구현됨 |

문서 상태는 `Draft`를 유지한다. SAL-REQ-042의 durable notification Outbox와 재시도/dead-letter 처리는 구현됐으며, 실제 Redis/SSE End-to-End와 PostgreSQL concurrency acceptance gate 검증은 아직 남아 있다. Client는 reconnect와 영속 API 재조회로 상태를 복구한다.
