# ADR-0048: Knowledge Collection sync execution boundary

Status: Accepted
Related ADRs: [ADR-0020](ADR-0020-knowledge-mcp-incremental-sync-boundary.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0034](ADR-0034-knowledge-delegated-administration-and-rbac-boundary.md), [ADR-0044](ADR-0044-knowledge-collection-operational-management-boundary.md)

## Context

Knowledge Collection permission에는 `sync` action과 Sync Operator(`read + sync`) bundle이
있고 domain delegation에는 `sync_manage`가 있다. Collection에는 `sync_state`도 있지만,
사용자가 KC sync를 명시적으로 요청하고 durable 상태를 확인하는 API/UI와 Celery 실행
경계는 연결되어 있지 않다.

Celery는 at-least-once delivery를 전제로 하므로 HTTP retry, 중복 click, message redelivery,
worker crash와 publish/commit gap을 함께 다뤄야 한다. 또한 Collection `sync`는 child KB
content 권한이 아니므로 status나 audit에 child identity와 source config를 노출해서는 안
된다. 현재 Workflow Engine이 실제로 재수집할 수 있는 source는 legacy DB document이며,
신규 MCP/API/source connector 구현은 MBA-265 범위 밖이다.

기존 Gateway ingestion은 `DocumentVersion`별 chunk를 준비한 뒤 active pointer를 교체하지만,
legacy vector save는 document의 모든 chunk를 지우고 unversioned chunk를 만든다. KC sync가
후자를 그대로 호출하면 active ready version이 있는 KB를 retrieval에서 사라지게 할 수 있다.
또한 job-level snapshot digest만 저장하고 item별 revision을 버리거나 live KB/document FK의
`ON DELETE CASCADE`에 item을 연결하면 요청 뒤 변경·삭제된 target을 성공으로 오인할 수 있다.

## Options Considered

1. Collection `sync_state`와 Celery result backend만 사용한다.
   - 장점: 새 table이 없다.
   - 단점: transaction, organization scope, child retry, partial failure, retention과 crash
     recovery의 durable source of truth가 없다.
2. Celery task ID와 autoretry만으로 중복 실행을 막는다.
   - 장점: worker 코드가 단순하다.
   - 단점: task ID는 broker dedupe가 아니며 DB commit 뒤 crash를 판별하지 못한다.
3. KC sync job/item table, idempotency key, active single-flight와 DB lease를 사용한다.
   - 장점: request, claim, target, retry, terminal 상태를 PostgreSQL constraint/transaction으로
     검증할 수 있다.
   - 단점: additive schema와 recovery worker가 필요하다.
4. Source/system-managed Collection까지 placeholder 성공으로 처리한다.
   - 장점: UI상 모든 KC가 동작하는 것처럼 보인다.
   - 단점: 실제 freshness를 갱신하지 않아 운영자에게 잘못된 성공 신호를 준다.
5. 현재 실행 가능한 Manual KC의 DB document만 지원하고 나머지는 fail-closed 한다.
   - 장점: 구현된 능력과 제품 표시가 일치한다.
   - 단점: 초기 지원 범위가 좁다.

## Decision

Option 3과 option 5를 채택한다.

### Authorization

- Sync request와 status 조회는 Organization manager, effective Collection `sync`, domain
  `sync_manage` 중 하나를 요구한다.
- Organization manager 판정은 ADR-0009의 membership-first helper를 먼저 사용한다. Active 또는
  non-active membership row가 있으면 그 row가 우선하고, membership row 자체가 없는 legacy
  `created_by`/`managed_by`만 manager fallback을 받는다. Manager가 아닌 actor는 active
  organization membership을 통과해야 resource/domain grant를 평가한다.
- Collection `manage`만으로 sync를 허용하지 않는다.
- `sync`/`sync_manage`는 child KB `read/use/write/manage` 또는 Collection `route`를 부여하지
  않는다.
- Gateway request와 Workflow worker claim에서 current authority를 각각 평가한다.
- Worker의 fresh authorization query를 concurrent revoke linearization point로 둔다. Revoke가
  query 전에 commit되면 cancel하고, query 뒤 commit되면 이미 시작한 bounded batch는
  완료하며 다음 claim부터 반영한다.

### Supported target

- Active Manual Collection의 active, non-source-managed child KB 중 legacy `documents` row가
  정확히 하나이고 그 row가 `SourceType.DB`인 document-level KB만 초기 sync target이다. DB
  document를 포함한 KB에 다른 DB/FILE/API document가 함께 있으면 KB-level active version
  pointer가 sibling content를 숨길 수 있으므로 fail-closed한다. 별도 FILE-only KB는 외부 sync
  target이 아니다.
- Target order는 Collection item rank, item created time, KB UUID, document UUID다.
- 각 job item은 Collection/membership/KB/document UUID, item rank/created time과 document updated
  time에서 계산한 per-target revision을 저장한다. Job-level revision은 document updated time을
  제외한 ordered membership topology revision의 aggregate다. Worker는 Collection lock 아래 claim과
  finalize에서 canonical target set을 다시 계산하고, shared document advisory lock과 target row lock을
  획득한 뒤 source I/O 전에는 per-target revision을 비교한다. Membership add/remove/reorder 또는
  child source composition 변경은 `sync.targets_changed`로 종료한다.
- Job item의 target UUID는 요청 시점 내부 snapshot reference이며 live KB/document에 cascading
  FK로 연결하지 않는다. 대신 `(job_id, organization_id, collection_id)` composite FK로 owning
  job과의 tenant/scope 일치만 강제한다. 요청 뒤 unlink나 live KB/document hard delete가 발생해도
  item은 retention 동안 남아 changed target으로 집계되어야 한다.
- System/source-managed Collection, source-managed child, API connector sync와 DB target을 포함한
  multi-document KB는 승인된 adapter/document-atom cutover가 없는 동안 fail-closed 한다.
- Target cap은 100, 한 delivery의 batch는 5, document concurrency는 1이다.
- Legacy `connections`에는 organization column이 없으므로 stored DB connection의 owner가 현재
  organization의 active member인지 worker가 검증한다. 불일치·inactive owner·지원하지 않는 DB
  type은 configuration failure로 닫고 connection/config 식별자는 외부로 투영하지 않는다.
- 문서 한 건의 source row limit은 초기 실행에서 최대 1,000으로 강제해 기존 저장 설정이 더
  크더라도 단일 job이 외부 DB와 embedding provider를 무제한 점유하지 않게 한다.
- Worker는 DB processor 결과에 문서에 저장된 flat selection(`all`, `range`, `keyword`)을 기존
  ingestion과 같은 helper로 적용한 뒤에만 새 version chunk를 저장한다. 필터 결과가 비면 새
  version을 활성화하지 않고 이전 active ready version을 유지한다.
- Stored DB selection의 table/column/JOIN 값은 SQL fragment가 아니라 PostgreSQL 단일
  identifier로 인용한다. JOIN edge는 snapshot에 선택된 두 table만 참조할 수 있고 `LIMIT`은
  bounded integer로 정규화한다. 저장 metadata가 변조되었더라도 임의 expression, 추가 table,
  statement를 실행하지 않고 configuration failure로 닫는다.

### Idempotency and single-flight

- Client는 canonical UUID `Idempotency-Key`를 보낸다. DB에는 SHA-256 hash만 저장한다.
- Organization+Collection+request hash unique는 같은 request retry를 기존 job으로 결합한다.
- Queued/running job은 Collection별 PostgreSQL partial unique constraint로 하나만 허용한다.
- Celery delivery ID는 dedupe authority로 사용하지 않는다. Continuation/recovery delivery는
  broker가 각 ID를 생성하고, job status/lease가 모든 중복 판정의 execution authority다.

### Commit, dispatch, lease and retry

- Gateway는 job/items, Collection pending, requested audit를 같은 transaction에 저장하고 commit
  뒤 job UUID 하나만 task로 발행한다.
- Publish 실패는 queued job으로 남고 Worker와 분리된 singleton Celery Beat의 periodic recovery가
  재발행한다. Docker Compose와 Helm 배포 모두 Beat를 포함하며 Helm rollout은 `Recreate` strategy로
  동시에 둘 이상의 scheduler가 실행되는 시간을 만들지 않는다.
- Task는 `acks_late`와 `reject_on_worker_lost`를 사용하되 business retry는 DB item attempt,
  next retry와 lease가 소유한다.
- Job claim 상한은 target 수의 정상 batch claim, 각 item의 최대 retry claim, stale recovery 5회를
  모두 수용하도록 target snapshot에서 계산한다. 100개 target의 초기 상한은 225이며 30분 overall
  deadline이 별도 시간 상한으로 유지된다.
- Unexpired running duplicate와 terminal redelivery는 no-op한다. Stale lease는 recovery가
  bounded retry하거나 deadline/max recovery 뒤 failed 처리한다.
- Target apply와 item success/progress는 가능한 한 같은 DB commit에 포함한다.
- Item `attempt_count`는 `running` 표시에 두 번 걸치지 않고 succeeded/failed/skipped outcome을
  영속하는 transaction에서 실제 실행당 한 번만 증가한다.
- KC sync와 기존 ingestion이 같은 document를 갱신하는 경로는 shared PostgreSQL transaction
  advisory lock으로 document 단위 직렬화한다. Lock key는 document UUID의 namespaced digest에서
  만들고 transaction commit/rollback과 함께 자동 해제한다. 모든 writer는 source fetch, parsing,
  embedding과 document/Collection/KB row lock보다 먼저 advisory lock을 획득한다. 상태를 indexing으로
  바꾸는 선행 commit이 필요하면 그 commit 직후 source read 전에 lock을 잡는다.
- Organization-scoped KB의 KC sync는 legacy unversioned delete/insert를 사용하지 않는다. 새
  `DocumentVersion(status=indexing)`과 version-scoped chunk를 같은 transaction에서 만들고, chunk가
  하나 이상 준비된 뒤 active pointer를 원자 교체한다. 실패·empty result·rollback은 이전 active
  ready version과 chunk를 유지하며, 성공한 뒤에만 이전 version을 superseded로 표시한다.
- Shared legacy unversioned vector save는 `document_version_id IS NULL` scope만 조회·삭제한다. 따라서
  pre-execution legacy SyncService가 실행되어도 active/historical version chunk를 삭제하거나 해당
  version의 embedding을 unversioned incremental reuse source로 섞지 않는다.

### Status, partial failure and retention

- Job status는 `queued`, `running`, `succeeded`, `partially_failed`, `failed`, `cancelled`다.
- 일부 success와 failed/changed target이 섞이면 `partially_failed`와 Collection `stale`, all
  failure는 `failed`, all success는 `succeeded`와 Collection `synced`다.
- 여러 failed/skipped item의 representative safe reason은 position 순 첫 행 하나만 조회하며,
  reason 집계가 다중 행 예외로 terminal finalization을 rollback하지 않아야 한다.
- Terminal 집계는 item row count를 job의 immutable `total_count`와 비교한다. 누락된 item은
  `sync.targets_changed` skipped로 보정하고, processed count가 original total과 정확히 일치하지
  않으면 success로 finalize하지 않는다.
- `source_deleted`는 job progress보다 우선하는 absorbing state다. Queue, recovery, cancel과 terminal
  projection은 이미 `source_deleted`인 Collection을 `pending/syncing/synced/stale/failed` 또는
  이전 state로 되살리지 않는다.
- User status는 범주형 progress와 allowlisted safe reason만 반환한다. Exact count와 child
  identity는 반환하지 않는다.
- User cancellation은 이번 범위에 포함하지 않는다. Permission/lifecycle invalidation은 claim
  전 system cancellation으로 처리하고 overall deadline은 30분으로 둔다.
- Terminal job/item은 기본 30일 뒤 bounded cleanup하며 canonical audit retention은 별도다.

## Rationale

PostgreSQL job/item과 single-flight는 HTTP와 Celery의 중복 전달을 한 경계에서 다룰 수 있다.
DB constraint, row lock, lease와 transaction을 사용하면 fake broker dedupe에 의존하지 않고
실제 동시성을 검증할 수 있다. Commit 뒤 publish와 recovery 조합은 worker가 아직 없는 row를
읽는 문제를 피하면서 broker 장애 뒤에도 durable request를 잃지 않는다.

지원 범위를 DB document로 제한하는 것은 임시 placeholder 성공보다 보수적이지만, 현재
코드가 실제로 제공하는 기능과 사용자에게 표시하는 freshness를 일치시킨다. Source-managed
sync는 MCP/API connector, source revision, ACL/public exposure와 content safety contract를
함께 구현해야 하므로 이 ADR에서 우회하지 않는다.

## Consequences

- `knowledge_collection_sync_jobs`와 `knowledge_collection_sync_job_items` additive table이
  추가된다.
- Job item은 per-target revision을 보존하고 live target delete cascade에서 분리된다.
- KC DB document apply는 canonical versioned ingestion finalizer를 사용하므로 실패한 refresh가
  이전 retrieval-visible version을 파괴하지 않는다.
- Gateway와 Workflow Engine에 각각 application/port/adapter/composition boundary가 생긴다.
- Docker Compose와 Helm의 별도 singleton Celery Beat는 due/stale job recovery와 terminal retention
  cleanup task를 실행한다.
- Client는 polling 기반 safe status panel을 제공한다.
- Collection management projection은 권한인 `can_sync`와 현재 adapter 지원 여부인
  `sync_supported`를 분리하고 sync POST와 같은 canonical child eligibility scan을 사용해 UI가
  unsupported source를 실행 가능하다고 표시하지 않게 한다.
- Sync Operator는 child content를 읽지 않고 운영 refresh를 요청할 수 있지만 raw child 결과를
  볼 수 없다.
- System/source-managed KC sync는 connector 구현 전까지 정책 오류로 남는다.

## Affected Official Documents

- [docs/architecture.md](../architecture.md)
- [docs/data_model.md](../data_model.md)
- [docs/glossary.md](../glossary.md)
- [docs/features/knowledge/requirements.md](../features/knowledge/requirements.md)
- [docs/features/knowledge/api_spec.md](../features/knowledge/api_spec.md)
- [docs/features/knowledge/component_spec.md](../features/knowledge/component_spec.md)
- [docs/features/knowledge/test_cases.md](../features/knowledge/test_cases.md)

## Follow-up Review Notes

- Source connector를 추가하는 PR은 stable source revision, cursor/watermark, source ACL freshness,
  public exposure와 content safety를 같은 job target contract에 추가해야 한다.
- API source ingestion을 shared port로 이관할 때 지원 target 확대를 검토한다.
- 운영 load test 뒤 target cap, batch size, lease와 retry backoff를 조정한다.
- Immediate mid-batch revoke 또는 user cancellation이 필요한 규제 요구가 생기면 cooperative
  cancellation/authorization epoch를 별도 ADR로 결정한다.
- Celery Beat가 없는 배포는 별도 dispatcher가 필요하며 queued job을 무기한 방치하면 안 된다.
- 향후 Gateway와 Workflow ingestion의 chunk preparation까지 하나의 application service로 합칠 수
  있지만, 현재 결정은 shared version-scoped persistence/finalization contract를 두 writer가
  동일하게 지키는 데 한정한다.

## Non-Goals

- 신규 MCP/API/source connector protocol
- Live connector authorization/cache
- Source-managed public exposure primitive
- Child KB permission 자동 부여
- Workflow pre-execution direct-KB sync의 전면 교체
- User cancellation과 child-level redrive UI
- Raw connector payload/status surface
