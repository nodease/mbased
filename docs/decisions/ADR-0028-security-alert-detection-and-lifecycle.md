# ADR-0028: Security Alert 탐지와 lifecycle 경계

Status: Accepted
Related ADRs: [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0023](ADR-0023-audit-actor-access-management-boundary.md), [ADR-0070](ADR-0070-organization-detector-provider-and-pre-embedding-local-masking-boundary.md)

## 배경

Nodease는 scope 안 resource permission 부족을 `permission.denied`, 정책 차단을 `policy.block`으로 `audit_logs`에 기록한다. 관리자는 audit 검색으로 개별 사건을 확인할 수 있지만, 같은 사용자가 짧은 시간 안에 차단 행동을 반복하거나 여러 resource를 탐색하는 패턴을 자동으로 발견하고 대응하는 기능은 없다.

Security Alert MVP는 검증된 organization과 인증 사용자가 있는 audit만 입력으로 사용한다. 인증 전 로그인 실패, IP/account fingerprint, 플랫폼 운영자 경보, 비용·실행 실패·대량 삭제 같은 운영 이상은 별도 후속 범위다.

현재 producer inventory에서는 같은 canonical action이라도 metadata 계약이 다르다.

- 공통 `record_resource_permission_denied()`에 검증된 `organization_id`를 전달하는 경로는 organization-scoped 탐지 입력을 제공한다.
- 기존 workflow/LLM credential/team/organization manager permission helper 일부는 actor와 target은 기록하지만 `audit_metadata.organization_id`를 기록하지 않는다.
- Actor access management의 `policy.block`은 최상위 `policy_reason`을 기록한다.
- RAG PII 차단은 legacy `policy_result.reason_code='pii_policy_blocked'`를 사용한다.
- ADR-0070 Target의 Knowledge ingestion terminal block은 MBA-362에서
  `knowledge.sensitive_content_detected` canonical `policy_reason` producer를 추가한다.
- Workflow 예산 초과는 `reason='budget.exceeded'`를 사용하며 보안 이상 접근이 아니라 운영 사건이다.

Alert worker가 resource table을 다시 조회해 organization이나 reason을 추측하면 삭제·scope 변경·hidden resource 정책에 따라 과거 사건의 의미가 달라질 수 있다. 따라서 audit 생성 시점에 검증된 provenance와 표준 reason을 저장해야 한다.

## 결정

### 1. 탐지 입력 경계

Security Alert 대상 audit은 다음 조건을 모두 만족해야 한다.

- `actor_id`가 존재한다.
- `actor_type='user'`다.
- `category='action'`이다.
- `status='failure'`다.
- `audit_metadata.organization_id`가 검증된 UUID다.
- action별 필수 target 또는 reason 계약을 만족한다.
- 기능 활성화 시점 이후 발생한 audit이다.

다음 사건은 입력에서 제외한다.

- `user.login_failed`
- 비로그인 또는 organization scope가 없는 `auth.permission_denied`
- organization 또는 actor가 누락되거나 유효하지 않은 사건
- scope 밖 resource를 숨긴 404
- validation 실패와 desired-state no-op
- 기능 활성화 이전 audit
- allowlist에 없는 `policy.block`

기능 활성화 이전 audit은 backfill하지 않는다. Reconciliation도 활성화 시점 이후의 audit만 처리한다.

### 2. `permission.denied` provenance

탐지 대상 `permission.denied` producer는 audit 생성 시점에 scope 검증을 마친 organization ID를 `audit_metadata.organization_id`에 명시적으로 기록한다.

필수 계약은 다음과 같다.

| Field | Contract |
| --- | --- |
| `actor_id` | 인증된 사용자 ID |
| `actor_type` | `user` |
| `category` | `action` |
| `status` | `failure` |
| `target_type` | scope 확인이 끝난 resource type |
| `target_id` | scope 확인이 끝난 opaque resource ID |
| `audit_metadata.organization_id` | 검증된 organization UUID |

Alert worker는 target resource를 다시 조회해 organization을 추론하지 않는다. Target scope가 확인되지 않았거나 target이 안전하지 않은 event는 distinct-target 계산에서 제외한다.

### 3. `policy_reason` 표준

모든 `policy.block` producer는 최상위 `audit_metadata.policy_reason`에 canonical reason을 기록한다.

Reason은 `{domain}.{reason}` 형식을 사용한다.

```text
^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$
```

- 모두 소문자를 사용한다.
- 단어는 `_`로 구분한다.
- 앞부분은 발생 domain, 뒷부분은 원인을 나타낸다.
- `blocked`, `failed`, `policy` 같은 결과 표현은 가능한 한 reason에 넣지 않는다.

MVP canonical reason은 다음과 같다.

| Domain | Canonical reason | Security Alert |
| --- | --- | --- |
| Actor access | `access_management.self_control_forbidden` | 포함 |
| Actor access | `access_management.last_active_manager` | 포함 |
| Actor access | `access_management.manager_override_active` | 포함 |
| Actor access | `access_management.member_state_not_manageable` | 포함 |
| Actor access | `access_management.target_user_inactive` | 포함 |
| Actor access | `access_management.stale_state` | 포함 |
| RAG evidence | `rag.pii_evidence_detected` | 포함 |
| Knowledge ingestion (Target MBA-362) | `knowledge.sensitive_content_detected` | 포함 |
| Budget | `budget.exceeded` | 제외 |

Legacy `pii_policy_blocked`는 읽기와 reconciliation 단계에서 `rag.pii_evidence_detected`로 정규화한다. Append-only 기존 audit row를 수정하거나 backfill하지 않는다. 기존 `policy_result.reason_code`와 `reason`은 호환 기간 동안 유지할 수 있지만 신규 탐지 로직은 canonical `policy_reason`만 사용한다.

### 4. 탐지 규칙

초기 규칙은 서버가 소유하는 고정 `v1` 계약이다. Organization별 rule 편집 UI는 만들지 않는다.

세 규칙의 ID/version, action, window, threshold, severity, count mode와 policy-reason grouping은 하나의 server-owned rule registry가 소유한다. Evaluator, alert aggregation과 worker 조회 window는 이 registry를 공유하며 같은 값을 별도 상수로 중복 정의하지 않는다.

| Rule ID | Detection key | Window and threshold | Severity |
| --- | --- | --- | --- |
| `repeated_permission_denied` | organization + actor + rule + version | 5분 안에 `permission.denied` 5회 | `medium` |
| `multi_resource_permission_probe` | organization + actor + rule + version | 10분 안에 서로 다른 안전한 target 5개에서 `permission.denied` | `high` |
| `repeated_policy_block` | organization + actor + rule + version + policy reason | 10분 안에 같은 `policy_reason`의 `policy.block` 3회 | `high` |

다른 organization, actor, rule version, policy reason은 합산하지 않는다. 동일 audit은 여러 규칙의 조건을 각각 만족할 수 있지만 같은 규칙에서 두 번 계산하지 않는다.

운영 반영 전 검증은 같은 evaluator를 사용하는 read-only replay로 수행할 수 있다. Replay는 지정 기간 이전의 최대 rule window만 lookback으로 읽고 지정 기간 event의 rule별 발화 횟수를 safe aggregate로 반환한다. Lookback 구간과 지정 평가 구간은 각각 bounded query로 완전하게 읽어야 하며, 어느 구간이든 limit을 초과하면 불완전한 집계를 반환하지 않고 safe reason code로 실패한다. Alert, evidence, lifecycle audit, notification과 reconciliation watermark는 변경하지 않으며 raw audit payload, target과 actor를 출력하지 않는다.

Distinct target은 `(target_type, target_id)` 조합이다. Target이 없거나 scope 안전성이 확인되지 않으면 `multi_resource_permission_probe`에서 제외한다.

### 5. 시간과 occurrence 계산

- 탐지 window는 `audit_logs.occurred_at` UTC를 기준으로 계산한다.
- Window 범위는 `window_start <= occurred_at <= current_event.occurred_at`로 양 끝을 포함한다.
- Audit 1건은 occurrence 1회다.
- `audit_log.id`를 idempotency key로 사용한다.
- Rule version은 초기 `v1`이며 threshold나 detection key 의미가 바뀌면 version을 올린다.

### 6. Cooldown과 재발

- `open`과 `acknowledged`는 활성 alert다.
- 같은 detection key에는 활성 alert가 최대 하나다.
- 마지막 탐지 시각부터 30분의 sliding cooldown을 적용한다.
- Cooldown 중 같은 detection key의 사건은 새 alert를 만들지 않고 기존 alert의 occurrence count와 `last_detected_at`을 갱신하며 evidence를 연결한다.
- Cooldown 종료 뒤 새 audit만으로 같은 rule threshold를 다시 충족하면 기존 활성 alert에 새 episode를 시작한다. 새 evidence를 연결한 transaction에서 `episode_count`를 한 번 증가시키고 `last_episode_started_at`을 threshold event time으로 갱신하며 commit 뒤 notification refresh를 다시 발생시킨다.
- `last_episode_started_at`은 notification 전달 성공 시각이 아니다. Notification publish 실패와 durable retry는 별도 delivery 경계가 소유한다.
- Cooldown 중 occurrence 갱신은 별도 audit action을 만들지 않는다.
- `resolved` alert에는 새 evidence를 연결하지 않는다.
- Resolve 이후 발생한 새 audit만으로 threshold를 다시 충족하면 새 alert를 생성한다.
- 이전 alert에 연결된 audit을 새 alert threshold 계산에 재사용하지 않는다.

### 7. Alert lifecycle

상태 전이는 다음과 같다.

```text
open -> acknowledged -> resolved
open -----------------> resolved
acknowledged ---------> open
```

- `acknowledged`는 관리자가 확인하고 조사 중인 상태다.
- `resolved`는 대응 완료 또는 오탐 처리가 끝난 상태다.
- Acknowledge와 resolve 전이 시점에는 인증·인가된 처리 관리자와 시각을 필수로 기록한다. 이후 해당 user가 삭제되면 `acknowledged_by`/`resolved_by` FK는 `SET NULL`이 될 수 있지만 처리 시각, resolution 정보, canonical audit은 보존한다.
- Resolve는 `mitigated`, `false_positive`, `accepted_risk` 중 하나의 resolution type과 sanitized reason을 필수로 기록한다.
- 해결된 alert는 삭제하지 않는다. Retention과 자동 삭제는 후속 정책으로 남긴다.
- 별도 read/unread 상태는 만들지 않는다.

Sidebar badge는 `open` alert만 센다. `acknowledged`와 `resolved`는 badge에서 제외하되 Admin Dashboard 검색에서는 조회할 수 있다.

### 8. Canonical alert action

다음 action을 canonical action으로 추가한다.

| Event | Action | Actor |
| --- | --- | --- |
| Alert 최초 생성 | `security_alert.detected` | `system` |
| 관리자 확인 | `security_alert.acknowledged` | 처리 관리자 |
| 조사 재개 | `security_alert.reopened` | 처리 관리자 |
| 해결 | `security_alert.resolved` | 처리 관리자 |

공통 계약은 다음과 같다.

- `category='action'`
- `target_type='security_alert'`
- `target_id=<security_alert_id>`
- 상태 변경 성공은 `status='success'`
- metadata에는 organization ID, rule ID/version, severity, sanitized resolution type/reason만 허용한다.

Alert 최초 생성, 최초 evidence 연결, `security_alert.detected` audit과 notification Outbox는 같은 DB transaction에 기록한다. Commit 이후 worker가 현재 manager 대상 notification 갱신 신호를 발행한다. Redis 발행이나 수신자 조회 실패는 alert transaction을 rollback하지 않고 최대 5회 재시도한 뒤 dead-letter 처리한다.

Lifecycle mutation은 현재 status와 `expected_version`을 조건으로 한 DB 원자적 변경에서 정확히 한 요청만 성공해야 한다. 성공 mutation과 canonical lifecycle audit은 같은 transaction에 기록하며 audit persistence 실패 시 mutation도 rollback한다.

### 9. 실시간 탐지와 reconciliation

Audit 저장 성공 이후 별도 비동기 task가 실시간 탐지를 수행한다. 탐지는 원래 authorization decision이나 사용자 응답을 지연하거나 변경하지 않는다.

Reconciliation은 실시간 task와 같은 판정 함수를 사용한다.

- `(occurred_at, audit_log.id)`를 cursor로 사용한다.
- Overlap window로 worker 중단과 경계 시각 누락을 복구한다.
- Evidence 연결 unique constraint와 audit ID idempotency로 중복 집계를 막는다.
- 실시간 task와 reconciliation이 경합해도 alert와 occurrence가 중복 생성되지 않아야 한다.
- 처리 목표는 event 발생 후 1분 이내 관리자 UI 반영이다.
- Reconciliation은 기능 활성화 이전 audit을 backfill하지 않는다.

### 10. 권한과 organization 경계

- 현재 active organization의 owner/manager만 Security Alert를 조회하고 상태를 변경할 수 있다.
- Alert 생성 당시의 manager 여부를 현재 권한 근거로 사용하지 않는다.
- Membership 정지·제거 또는 manager 강등은 즉시 접근을 차단한다.
- 일반 member와 audit 조회 전용 auditor/raw auditor는 Security Alert에 접근할 수 없다.
- 다른 organization 또는 scope 밖 alert ID는 404로 숨긴다.
- UI 권한 차단은 UX 보조이며 Gateway가 최종 판단한다.

### 11. 데이터 최소화와 표시

- Alert와 notification에 raw email, IP, user-agent, exception, request body, token, credential, trace/document payload를 저장하지 않는다.
- Hidden resource의 이름, 경로, 원문 metadata를 alert에서 복원하지 않는다.
- Actor 이름과 이메일 snapshot을 alert row에 복사하지 않는다.
- 표시 시 현재 권한 경계 안에서 safe user projection을 조회한다.
- 사용자가 삭제됐거나 표시할 수 없으면 opaque actor ID 또는 삭제된 사용자 상태로 표시한다.
- SSE event는 source of truth가 아니며 재조회 신호와 최소 safe summary만 전달한다.

## 검토한 선택지

### 조회 시점 집계

Admin Dashboard를 열 때마다 audit를 집계하는 방식이다. 별도 worker가 필요 없지만 대량 audit 검색 비용이 크고 alert 상태, acknowledge/resolve, cooldown, notification을 안정적으로 제공하기 어렵다.

### 실시간 탐지만 사용

지연은 작지만 worker 중단, task publish 실패, 배포 중 공백을 복구할 수 없다.

### 실시간 탐지와 reconciliation 병행

실시간 반영과 누락 복구를 함께 제공한다. Idempotency와 concurrency 설계가 필요하지만 DB에 보존되는 관리자 대응 alert에 가장 적합하므로 채택한다.

## 비범위

- 인증 전 로그인 실패와 비로그인 `auth.permission_denied`
- IP/account fingerprint와 credential-stuffing 탐지
- 플랫폼 운영자 경보
- 비용 급증, 실행 실패, 대량 삭제 등 운영 이상
- 이메일, Slack, SIEM 등 외부 전달
- Organization별 rule/threshold 설정 UI
- ML 기반 이상 탐지
- 자동 계정 정지 또는 자동 권한 회수
- 기존 audit backfill
- Alert retention과 자동 삭제
- Severity 자동 상승

## 영향

- 탐지 대상 audit producer는 검증된 organization ID와 canonical policy reason을 제공해야 한다.
- `AuditAction`과 ADR-0008 canonical action table에 `security_alert.*` 네 action을 추가한다.
- Security Alert는 invitation notification과 다른 영속 source of truth를 가진다.
- Admin Dashboard와 Sidebar는 Security Alert 전용 API projection을 사용한다.
- 후속 구현은 alert/evidence DB 모델, 실시간 detector, reconciliation, 관리자 API, Sidebar/Admin UI 순으로 진행한다.

## 후속 작업

- 탐지 대상 audit organization/reason 정규화: MBA-223
- Alert/evidence 영속 모델과 lifecycle: MBA-211
- 실시간 탐지와 reconciliation: MBA-212
- 관리자 조회·상태 변경 API: MBA-213
- Admin Dashboard, Sidebar, SSE: MBA-214
- Security Alert feature 문서 4종과 기존 active 문서 정렬: MBA-210
