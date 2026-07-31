# ADR-0052: Knowledge document ingestion durable execution boundary

Status: Accepted
Related ADRs: [ADR-0014](ADR-0014-knowledge-base-document-atom-and-collection-boundary.md), [ADR-0017](ADR-0017-knowledge-integration-provisional-implementation-baseline.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0048](ADR-0048-knowledge-collection-sync-execution-boundary.md), [ADR-0051](ADR-0051-connection-use-authorization-boundary.md)

## Context

Document process, sync, approval-resume와 embedding model reindex는 HTTP request 안에서
`BackgroundTasks`를 등록한 뒤 process-local DB session을 재사용했다. 이 구조는 Gateway
restart, commit 뒤 task 등록 실패, multi-replica 실행과 worker crash 뒤에 durable한 작업
존재·소유권·재시도 상태를 남기지 못한다. Celery task id와 Redis lock은 broker dedupe나
DB finalization 권위가 아니므로 이 공백을 닫을 수 없다.

Ingestion은 source read, parsing, chunking, embedding과 active document version swap을 포함한다.
오래된 worker가 lease 만료 뒤에도 진행률이나 active pointer를 갱신하면 이전 ready version을
가리거나 새 attempt의 결과를 덮을 수 있다. Queue payload에 source config나 parser input을
복제하면 retry durability를 얻는 대신 민감정보 노출면을 넓히게 된다.

## Options Considered

1. FastAPI `BackgroundTasks`와 process-local session을 유지한다.
2. Celery task id/autoretry를 실행 권위로 사용한다.
3. PostgreSQL durable job, document single-flight, worker lease/heartbeat/fencing과 due recovery를
   사용하고 Celery는 job UUID를 전달하는 wake-up 신호로만 사용한다.
4. 기존 Workflow Engine worker에 parser/storage 의존성을 추가한다.
5. Gateway image를 사용하는 `knowledge` queue 전용 worker를 둔다.

## Decision

Option 3과 option 5를 채택한다.

### Admission and idempotency

- `process`, `sync`, `resume`, `reindex`는 `knowledge_document_ingestion_jobs`를 durable source of
  truth로 사용한다.
- Gateway는 organization/KB/document를 현재 권한으로 확인하고 document 또는 KB row를 잠근다.
  Document queued 상태·설정과 job insert를 같은 transaction에 저장한 뒤 commit한다.
- Admission, reindex와 worker finalization은 `KnowledgeBase -> Document` 순서로 row lock을 획득한다.
  DB source process/sync는 ADR-0053에 따라 owner Connection row를 먼저 잠근 뒤 durable repository가
  `KnowledgeBase -> Document -> job`을 fresh lock한다. 최초 조회 Document revision은 새 admission 전에
  다시 비교하고, Connection lifecycle UoW가 sanitized reference metadata와 job의 flush/commit을 함께
  소유한다. DB source 설정은 ADR-0051의 owner 검증과 sanitized Connection reference만 유지한다.
- 같은 document에는 `pending|running|retry_scheduled` job 하나만 허용한다. 같은 active intent는
  기존 job을 재사용하고 다른 intent는 safe conflict로 닫는다.
- Job idempotency key는 organization, document, operation, generation과 protected input revision의
  SHA-256 digest로 만든다. Raw source path/config/content는 key, job metadata, Celery payload와 log에
  저장하지 않는다.
- Commit 뒤 publisher는 job UUID 하나만 `knowledge` queue로 전송한다. Publish 실패는 request
  transaction을 되돌리지 않으며 periodic recovery가 due job을 다시 발행한다. Recovery가 발행 대상으로
  고른 `pending|retry_scheduled` job에는 DB clock 기반의 bounded dispatch lease를 먼저 기록한다. Lease가
  유효한 동안 같은 job을 다시 발행하지 않으며 broker 전달 자체의 exactly-once를 주장하지 않는다.

### Worker claim, retry and finalization

- Worker는 job row를 `FOR UPDATE`로 읽고 terminal, valid running lease와 future retry를 실행하지
  않는다. 실행 직전에 requester의 current organization membership과 KB write 또는 sync authority를
  다시 확인한다.
- Claim은 opaque owner token, fencing token, PostgreSQL `clock_timestamp()` 기반 lease와 heartbeat를 기록한다. 장기 ingestion transaction의 시작 시각에 고정되는 `now()`는 lease 판정에 사용하지 않는다. Heartbeat는
  본 실행과 다른 DB session을 사용한다. Owner token과 fencing token이 일치해도 execution lease가 DB
  clock 기준으로 만료되었으면 heartbeat, worker lock과 success finalization을 모두 거부한다.
- Retryable failure는 bounded exponential backoff와 `next_retry_at`을 저장한다. Future retry를 즉시
  재발행하지 않고 due recovery scanner가 발행 책임을 소유한다. Soft time limit과 allowlisted
  transient failure만 자동 retry하며 unknown failure는 safe dead-letter로 닫는다. Processor의 raw
  error 문자열은 retry 판단에 사용하지 않고 orchestration 경계에서 allowlisted typed source reason으로
  정규화한다. Remote FILE processor는 `EgressGuardError`의 typed reason을 generic exception으로 감싸지 않는다. DB/API와 remote FILE egress의 timeout, connection, DNS 및 API 408/425/429/5xx만
  transient source failure로 분류한다. Private target 등 egress policy 차단은 retry하지 않는다.
- Retry exhaustion은 `dead_lettered`로 남긴다. 권한 있는 manual retry는 terminal row를 되살리지
  않고 새 generation job을 만든다.
- 최초 worker claim과 Recovery는 후보를 찾은 뒤 admission/finalization/failure transition과 같은
  `KnowledgeBase -> Document -> job` 순서로 canonical row lock을 획득하고 due 상태를 다시 확인한다.
  Recovery만 scope row에 `SKIP LOCKED`를 적용해 실행 중인 첫 문서를 기다리지 않고 이후 due job과
  terminal cleanup을 계속한다. Retry/dead-letter 전이와 pending/retry job의 dispatch lease 획득은 이
  잠금 아래 수행해 역순 잠금 교착을 만들지 않는다.
- Active version finalization, Document completed projection과 job succeeded 전이는 같은 DB
  transaction에서 fencing token을 확인해 확정한다. Chunk progress 발행 전에는 별도 짧은 DB session으로 current owner/fence와 실제 wall-clock lease를 확인한다. Stale worker의 progress와 finalization은
  거부한다. Chunk 준비 단계의 advisory progress는 99 이하로 제한한다. 완료 progress=100도 이
  transaction에 포함하며 성공 commit 뒤에만 Redis advisory 100을 알린다. 새 admission과
  retry/cancel/dead-letter/recovery 전이는 canonical DB commit 뒤 해당 document의
  Redis key를 삭제해 새 attempt 또는 DB projection으로 fallback한다. Lease를 잃은 worker는 cache를
  삭제하지 않는다. Redis progress는 권위 상태가 아니다.
- Terminal job은 기본 30일 뒤 bounded cleanup한다. Canonical audit와 document version retention은
  별도 정책을 따른다.

### API and rollout

- Process/sync/resume/reindex는 durable admission 성공 뒤 `202` 또는 기존 update 응답을 반환한다.
  Status API는 allowlisted job state만 `Cache-Control: no-store`로 반환한다.
- Retry/status/progress와 processing document 상세 reconciliation을 포함해 새 table을 사용하는 API는 권한 확인 뒤 schema readiness를 검사하고
  stale schema를 raw DB 오류가 아닌 `503 knowledge.ingestion_schema_not_ready`로 닫는다.
- Approval resume의 동일 intent 재요청은 첫 admission이 Document를 `indexing`으로 바꾼 뒤에도 active job을 재사용한다. Required status는 새 job을 만들 때만 적용한다.
- Content/model/chunking이 unchanged인 no-op은 KB active ready version의 `legacy_document_id`가 현재 Document와 일치할 때만 허용한다. Legacy multi-document pointer가 다른 Document를 가리키면 정상 재색인한다.
- `knowledge` worker는 Gateway image/parser/storage 의존성을 사용하고 다른 Celery queue를 소비하지
  않는다. Worker bootstep은 필수 table, column, unique constraint와 index가 없으면 queue 소비 전에
  startup을 실패시킨다. Compose worker는 migration을 수행한 Gateway health 이후 시작하며 Kubernetes
  worker는 bounded init readiness를 통과한 뒤 Celery bootstep에서 다시 fail-closed 검사한다. Helm
  chart가 ServiceAccount 생성을 소유하는 설정에서는 worker가 참조하는 동일 이름의 ServiceAccount를
  렌더링하고, 외부 ServiceAccount를 사용하는 설정에서는 chart가 새 리소스를 생성하지 않는다.
- `STORAGE_TYPE=LOCAL`로 전용 worker를 활성화하면 Gateway와 worker는 동일 upload PVC를 마운트해야
  하고 non-root container가 쓸 수 있도록 명시한 fsGroup을 적용해야 한다. Helm은 shared local storage
  설정이 없으면 rendering을 거부한다. Production `CLOUD` storage는 이 PVC를 사용하지 않는다.
- Production rollout은 additive migration을 먼저 적용한 뒤 Knowledge worker를 활성화한다. Queue
  drain 뒤 application rollback은 가능하지만 schema downgrade를 rollback 수단으로 사용하지 않는다.
- Provider-neutral Helm production reference는 bounded Knowledge worker와 singleton Celery Beat를
  함께 활성화한다. Worker replica 또는 concurrency가 양수가 아니거나 recovery schedule을 소유한
  Beat가 꺼져 있으면 chart rendering을 safe fixed error로 거부한다. 이는 migration을 Helm이
  대신 수행한다는 뜻이 아니며 운영자는 release migration을 worker rollout보다 먼저 적용해야 한다.
- Kubernetes init container와 Celery bootstep의 schema/keyring 검사를 통과한 worker만 시작한다.
  실행 중 readiness는 `knowledge@<pod-hostname>` exact destination에 대한 bounded Celery self-ping으로
  해당 Pod의 consumer와 broker control path를 확인한다. 다른 replica의 pong은 local readiness로
  인정하지 않는다. Redis/control path 장애는 Pod를 `Unready`로 표시하되 liveness restart loop를
  만들지 않으며, probe는 raw broker/credential/exception detail을 출력하지 않는다.

## Consequences

- Gateway process 생명주기와 document ingestion 생명주기가 분리된다.
- Broker 장애나 Gateway restart 뒤에도 committed job은 recovery 가능하다.
- Duplicate Celery delivery는 가능하지만 DB claim과 fencing 때문에 같은 active generation을 둘이
  finalize할 수 없다.
- Parsing/embedding provider 호출 자체의 수학적 exactly-once는 보장하지 않는다. Lease 만료 뒤
  provider 작업이 겹칠 수 있으나 stale DB write/finalization은 차단한다.
- Production readiness는 local Celery consumer가 control message에 응답한다는 운영 신호다. 개별
  ingestion 성공, queue drain 또는 provider/storage 정상 동작을 보증하지 않으며 durable job/status가
  계속 실행 결과의 source of truth다.
- KC sync job/item은 ADR-0048의 별도 aggregate를 유지한다. MBA-288은 Collection batch sync를
  대체하거나 source connector adapter를 추가하지 않는다.

## Affected Official Documents

- [docs/architecture.md](../architecture.md)
- [docs/data_model.md](../data_model.md)
- [docs/glossary.md](../glossary.md)
- [docs/features/knowledge/requirements.md](../features/knowledge/requirements.md)
- [docs/features/knowledge/api_spec.md](../features/knowledge/api_spec.md)
- [docs/features/knowledge/component_spec.md](../features/knowledge/component_spec.md)
- [docs/features/knowledge/test_cases.md](../features/knowledge/test_cases.md)

## Non-Goals

- Source connector protocol/cursor/ACL adapter 구현
- KC sync job/item aggregate 통합
- Parser sandbox 또는 malware scanner 전면 구현
- Provider 호출의 exactly-once 보장
- User cancellation과 per-stage UI
- Raw source payload 또는 내부 worker token의 API/trace 노출
