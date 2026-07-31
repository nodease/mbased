# ADR-0042: Security Alert reconciliation receipt 경계

Status: Accepted
Related ADRs: ADR-0028

## Context

ADR-0028의 Security Alert reconciler는 `audit_logs.occurred_at`과 audit ID를 durable cursor로 사용하고 1분 overlap을 다시 읽는다. 이 cursor는 event 발생 순서는 표현하지만 PostgreSQL commit 순서는 표현하지 않는다. Producer가 오래 대기한 뒤 과거 `occurred_at` audit를 commit하고 실시간 `security_alert.detect` 발행도 실패하면, reconciler가 이미 overlap 경계를 지나 해당 audit를 영구 누락할 수 있다.

`ingested_at` timestamp나 sequence를 audit insert 시점에 부여하는 방식도 transaction commit 순서가 뒤집힐 수 있어 누락 방지 기준으로 충분하지 않다. AuditLog는 여러 service에서 append-only로 생성되므로 특정 producer에만 별도 enqueue를 추가하는 방식도 전체 저장 경로를 보장하지 못한다.

## Options Considered

1. Event-time cursor의 overlap을 늘린다.
   - 구현은 작지만 저장 지연의 상한을 보장할 수 없고 scan 비용과 중복 평가가 계속 증가한다.
2. `ingested_at` 또는 sequence cursor를 추가한다.
   - 도착 순서를 별도로 표현할 수 있지만 값 할당과 commit 순서가 뒤집히면 cursor가 늦은 transaction을 건너뛸 수 있다.
3. Processor별 처리 완료 receipt를 저장하고 receipt가 없는 audit를 조회한다.
   - 행이 언제 commit됐는지 추론하지 않고 처리 완료 여부를 직접 증명할 수 있지만 audit별 작은 receipt row가 추가된다.

## Decision

Security Alert reconciliation은 `security_alert_reconciliation_receipts`를 durable discovery 기준으로 사용한다. Receipt의 primary key는 `(processor_name, audit_log_id)`이고 최초 발견 batch인 `discovered_generation`, 마지막 평가 batch인 `evaluated_generation`, `processed_at`을 저장한다. Watermark는 성공한 `reconciliation_generation`을 저장한다. `audit_log_id`에는 FK를 두지 않아 append-only audit의 별도 보존·삭제 lifecycle과 reconciliation 기록을 결합하지 않는다.

Reconciler는 watermark row를 잠근 뒤 `activation_started_at` 이상인 audit 중 같은 processor receipt가 없는 행을 발견 기준으로 사용한다. 늦은 eligible audit가 이미 처리된 후속 audit의 threshold 결과를 바꿀 수 있으므로, 같은 organization·actor·action 범위에서 해당 audit부터 `SECURITY_ALERT_MAX_WINDOW` 안에 있는 receipt 보유 후속 audit도 함께 `(occurred_at, audit_log.id)` 순서로 다시 평가한다. 한 실행은 이 순서에서 최대 100건만 읽는다. Batch 경계 뒤에 남은 후속 audit는 선행 audit의 `discovered_generation`이 자신의 `evaluated_generation`보다 큰 동안 stale 상태로 남아 다음 실행에서 이어서 처리된다. 후속 audit 재평가 시 최초 `discovered_generation`은 유지하고 `evaluated_generation`만 갱신하므로 재평가가 원래 late audit의 rule window 밖으로 연쇄 확장되지 않는다. 다른 organization·actor·action 범위와 window 밖 audit는 재평가하지 않는다. `occurred_at`은 rule window 평가와 안정적인 처리 순서에 계속 사용하지만, 새 audit 발견 여부를 제한하는 cursor로 사용하지 않는다.

각 batch의 audit rule 평가, Alert/evidence 갱신, notification Outbox 생성, receipt upsert와 watermark generation/cursor 전진은 같은 transaction에 포함한다. Eligible하지 않아 Alert를 변경하지 않은 audit도 성공적으로 평가했으면 receipt를 남긴다. 처리 또는 commit이 실패하면 해당 batch 전체가 rollback되어 retry가 같은 audit부터 다시 시작하고, 앞서 commit된 batch는 유지된다.

`security_alert_reconciliation_watermarks`는 additive compatibility를 위해 유지한다. `activation_started_at`은 기능 활성화 이전 audit을 제외하는 정책 경계이고 기존 `(cursor_occurred_at, cursor_audit_log_id)`는 마지막 성공 event-time 관찰값이다. `reconciliation_generation`은 batch 경계의 durable 진행 번호다. Event-time cursor와 overlap은 더 이상 누락 방지의 correctness 기준이 아니다.

Migration은 기존 audit를 receipt로 backfill하지 않는다. 기존 receipt의 generation은 0으로 시작하고 첫 bounded batch부터 증가한다. 기존 non-empty 환경의 활성화 이후 audit는 최대 100건씩 idempotent하게 재평가된다. 로컬 개발 데이터는 필요하면 migration 전에 재생성한다.

## Rationale

- Commit 순서를 추정하지 않고 처리 완료 사실을 직접 저장해 late-arrival 누락을 막는다.
- 늦은 audit 뒤의 bounded rule window만 다시 평가해 이미 receipt가 있는 후속 event의 threshold 결과도 복구한다.
- 실시간 task, retry와 reconciliation이 공유하는 audit ID 멱등성 제약을 그대로 사용한다.
- Audit producer 전체를 중앙화하거나 trigger를 추가하지 않고 reconciler 경계 안에서 변경을 제한한다.
- Receipt 부재와 generation 차이를 bounded query와 부분 진행 기준으로 함께 사용한다.

## Consequences

- 활성화 이후 audit마다 processor별 receipt 저장 공간이 필요하고 AuditLog와 같은 기간 보존한다.
- Backlog가 100건을 넘으면 다음 1분 주기까지 복구가 이어지므로 전체 복구 시간은 backlog 크기에 비례한다.
- Poison event를 성공 receipt로 바꾸어 자동 skip하지 않는다. 실패는 receipt 없이 남아 안전한 retry 대상으로 유지하며 격리·manual redrive가 필요하면 별도 결정으로 추가한다.
- ADR-0028의 event-time cursor와 overlap은 역사적 설계로 남고, reconciliation discovery correctness는 이 ADR이 보정한다.

## Affected Files

- `apps/shared/db/models/security_alert.py`
- `apps/shared/services/security_alert_reconciliation.py`
- `apps/shared/alembic/versions/`
- `apps/log_system/tests/test_security_alert_reconciliation.py`
- `apps/shared/tests/db/test_security_alert_disposable_postgres.py`
- `docs/features/security-alert/`
