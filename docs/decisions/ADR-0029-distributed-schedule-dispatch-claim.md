# ADR-0029: Distributed Schedule Dispatch Claim

Status: Accepted
Related ADRs: [ADR-0008](ADR-0008-audit-action-naming-standard.md), [ADR-0018](ADR-0018-workflow-rag-anonymous-public-only-runtime.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0023](ADR-0023-audit-actor-access-management-boundary.md)
Deployment surface correction: coordinated workflow와 raw Kubernetes rollout 조항은 [ADR-0068](ADR-0068-eks-support-surface-removal.md)에 따라 future provider-neutral CD의 Target으로 유지한다. 현재 지원 Helm/Compose는 disabled mode만 허용하며 runtime claim ledger 결정은 그대로 유효하다.


## Context

Gateway는 process마다 APScheduler를 시작하고 production은 여러 Gateway replica를 실행한다. 각 replica가 같은 active Schedule을 memory job으로 로드하면 동일 cron occurrence가 replica 수만큼 Celery에 전송될 수 있다. Stale Schedule/Deployment 재검증은 잘못된 row를 차단하지만 여러 replica가 같은 유효 row를 동시에 읽는 경쟁은 막지 못한다.

Celery delivery도 exactly-once가 아니다. DB commit과 broker publish는 하나의 transaction이 아니며 duplicate delivery, publish 결과 기록 실패, Worker 종료가 발생할 수 있다. Admission 이후 workflow를 자동 재실행하면 외부 HTTP, email, ticket, DB mutation 같은 node 부수효과가 반복될 수 있다.

Schedule 실행에는 requester가 없다. App creator를 실행 사용자, audit actor 또는 RAG execution subject로 사용하면 실행 이력과 private Knowledge 권한이 잘못 귀속된다.

## Decision

### Canonical occurrence and claim

- 동일 occurrence는 canonical `schedule_id + scheduled_for`로 식별하고 unique constraint로 하나만 허용한다.
- deterministic idempotency key와 Celery task id는 occurrence에서 생성한다.
- `schedule_dispatch_claims`는 claim, publish, admission, terminal 상태를 보존하는 operational ledger다.
- claim 생성과 `Schedule.next_run_at` 전진은 같은 DB transaction에서 수행한다.
- DB commit 뒤 broker publish를 수행하며, publish 전 장애는 durable pending claim과 bounded recovery scanner가 복구한다.

Claim은 `schedule_id`, `organization_id`, `deployment_id`를 FK 없는 durable reference로 보존한다. `organization_id`는 권한 주체가 아니라 tenant/audit provenance다. Canonical App에 organization이 없거나 claim과 App organization이 다르면 fail-closed한다. Schedule/Deployment/Organization lifecycle 삭제가 claim을 cascade 삭제하지 않는다.

### State and admission

Claim 상태는 `pending`, `dispatching`, `enqueued`, `running`, `succeeded`, `canceled`, `dead_lettered` allowlist를 사용한다. 상태별 lease, timestamp, workflow run correlation과 safe reason 조합은 DB check constraint와 pure domain validation을 함께 적용한다.

Gateway replica는 PostgreSQL row lock, `SKIP LOCKED`, lease와 compare-and-set으로 pending claim을 분배한다. Broker network call 중에는 DB row lock을 유지하지 않는다.

Workflow Engine은 claim row를 잠그고 canonical Schedule/Deployment/App/runtime policy를 재검증한다. `enqueued` 또는 허용된 early-delivery `dispatching` claim을 `running`으로 원자적으로 전이한 Worker만 engine을 시작한다. Queue가 전달한 organization, workflow, app, deployment, version, execution subject는 권한 source of truth가 아니다.

Admission winner가 claim을 `running`으로 바꾸는 같은 원자적 write에서 stable `workflow_run_id`를 생성·저장한다. `pending`, `dispatching`, `enqueued`에는 run id가 없으며 duplicate delivery는 새 run identity를 만들거나 engine을 다시 시작하지 않는다.

`running`, `succeeded`, admission 이후 `dead_lettered` claim의 stable `workflow_run_id`가 configured visibility grace 이후에도 Log System의 `WorkflowRun` row로 확인되지 않으면 recovery scanner는 claim에 one-time reported timestamp와 safe audit을 기록한다. 이 signal은 queue 지연/누락의 관측 근거이며 Log System row, workflow, node, provider effect를 재구성하거나 replay하지 않는다.

### Failure and replay

- Admission 전 publish/budget transient failure만 bounded retry한다.
- Admission 이후 결과가 불명확하면 `dead_lettered/execution_outcome_unknown`으로 격리하고 자동 replay하지 않는다.
- Fixed execution deadline은 결과 불명 분류 기준이며 Worker 강제 종료나 실패 확정 근거가 아니다.
- Outcome unknown acknowledgment는 조사 완료와 rollback gate 해제 표시일 뿐 redrive 권한이 아니다.
- 외부 provider별 idempotency와 node side-effect exactly-once는 MBA-190 범위다.
- `attempt_count`는 Gateway dispatcher 처리 주기에서만 정확히 한 번 증가한다. Worker budget unavailable은 같은 published attempt를 재사용하고, 최대치에 도달한 pending claim은 publish하지 않는다.
- Admission 전 dead-letter reason은 run/start correlation을 금지하고 admission 후 reason은 task/enqueue/start/run correlation을 모두 요구한다. Domain과 DB constraint를 같은 matrix로 유지하며 기존 모순 row는 migration에서 추측 보정하지 않는다.
- `canceled`/`dead_lettered` terminal claim은 allowlisted `safe_reason_code`를 반드시 가지며, outcome review marker/audit/resolution은 모두 null이거나 모두 유효한 non-null 값이어야 한다. PostgreSQL CHECK의 `UNKNOWN` 통과에 의존하지 않는다.
- `pending`, `dispatching`, `enqueued`는 `workflow_run_id`를 가질 수 없다. Stable run identity는 Worker admission winner가 확정한다.
- 한 claim의 publish 결과 write 실패는 같은 prepared batch의 후속 publish를 중단하지 않고 lease recovery로 수렴한다. Engine 결과 확정 뒤 terminal CAS write는 fresh DB session으로 bounded 재시도하되 engine을 다시 실행하지 않는다.

### System actor and Knowledge boundary

- System schedule의 `WorkflowRun.user_id`는 `NULL`이다.
- Schedule execution audit은 `actor_id=NULL`, `actor_type=system`이다.
- App creator, deployment creator, workflow owner를 executor 또는 RAG execution subject로 합성하지 않는다.
- 명시적인 service account/assigned operator가 없는 schedule RAG는 ADR-0018의 anonymous public-only 경계를 사용한다.
- LLM provider credential이 필요한 경우 locked canonical Deployment의 `created_by`를 user형 credential principal로만 전달한다. Queue는 principal을 지정할 수 없고, principal은 executor/audit actor/Knowledge execution subject로 승격되지 않는다. 별도 service account 모델은 이 ADR에서 도입하지 않는다.
- System schedule의 LLM credential/model permission denial도 credential principal을 user actor로 기록하지 않고 `actor_id=NULL`, `actor_type=system`으로 남긴다.

### Audit and transaction

Schedule occurrence, budget decision, claim, next-run advancement와 필요한 policy audit는 application use case가 소유하는 한 UnitOfWork에서 commit한다. Repository, queue adapter, audit adapter는 commit/rollback을 호출하지 않는다.

Schedule audit은 access-management command/recorder를 재사용하지 않고 deployment application의 전용 audit port와 SQLAlchemy adapter를 사용한다. Adapter는 system actor와 strict metadata allowlist만 기록한다.

예산 차단은 occurrence, Gateway dispatch, Worker admission 어느 단계에서 확인되더라도 canonical `policy.block`, workflow target, `reason='budget.exceeded'`, `trigger_mode='scheduler'`로 같은 transaction에 기록한다. 재시도 한도 소진 claim은 `schedule_dispatch.failed`와 failure status를 사용하며 재평가 예정인 `schedule_dispatch.deferred`와 구분한다.

Outcome review의 canonical audit은 다음과 같다.

- action: `schedule_dispatch.outcome_reviewed`
- category/status: `action` / `success`
- target: `schedule_dispatch_claim`과 exact claim id
- metadata: `organization_id`, allowlisted `operation_correlation_id`, `outcome_resolution_code`
- 금지: raw exception, incident note, username/email, URL, branch/commit message, workflow input/output

Audit recorder가 생성한 id만 claim의 `outcome_review_audit_id`로 저장한다. CLI는 audit id나 임의 actor id를 입력받지 않는다. Human operator identity와 승인은 protected GitHub Environment 또는 Kubernetes IAM audit가 소유한다.

WorkflowRun visibility signal의 canonical audit은 `schedule_dispatch.workflow_run_missing`이며 target은 exact `schedule_dispatch_claim`이다. Metadata는 canonical `organization_id`와 `reason='workflow_run_missing'`만 허용한다. Signal 기록과 claim marker update는 같은 UnitOfWork에서 한 번만 commit하며 raw run id, input, output, provider response 또는 exception을 저장하지 않는다.

`Schedule.next_run_at`과 `last_run_at`은 system operational cursor다. Generic ORM configuration audit에서는 이 두 field만 제외하고 cron, timezone, activation/lifecycle 변경 audit은 유지한다. Claim model은 generic ORM audit listener 대상에 추가하지 않는다.

오래 중단된 고빈도 schedule은 과거 occurrence를 반복 열거하지 않고 `now`와 기존 cursor 중 더 늦은 시각을 기준으로 첫 미래 fire time에 coalesce한다. Catch-up 개수가 많다는 이유만으로 valid cron을 configuration error로 격리하지 않는다.

### Layer and composition

Gateway는 ADR-0022의 deployment application package를 확장한다.

- SchedulerService: process lifecycle과 periodic tick inbound adapter
- Application use case: occurrence claim, dispatch, outcome review transaction orchestration
- Port: repository/UnitOfWork, budget decision, audit recorder, next-fire calculator, task publisher
- Adapter: SQLAlchemy repository/audit, Celery publisher
- Composition: existing deployment composition root에서 concrete dependency wiring

Gateway schedule application은 access-management application model/port/recorder를 import하지 않는다. Gateway는 MBA-188의 generic SQLAlchemy UnitOfWork wrapper를 concrete dependency로 재사용할 수 있지만 Workflow Engine은 Gateway adapter를 역참조하지 않는다.

### Rollout

Migration을 먼저 적용하고 application은 `disabled` mode로 배포한다. 구버전 direct dispatcher와 신버전 claim dispatcher가 동시에 활성화되지 않도록 queue/task drain 뒤 `claim` mode를 활성화한다. Rollback은 `claim -> drain -> disabled` 순서로 수행한다. Nonterminal claim, review되지 않은 outcome unknown, active/queued/reserved schedule task가 남아 있으면 rollback을 중단한다. 이는 application rollout rollback이며, schedule branch의 migration downgrade는 system schedule executor history, Log System row가 아직 없는 admitted claim, active/unreviewed claim, quarantine state를 임의로 버리지 않도록 모든 schedule revision의 첫 DDL 전에 fail-closed한다.

`disabled`는 명시적인 kill switch이자 rollout 중간 상태다. 이 모드에서 신규 claim 또는 legacy APScheduler enqueue를 수행하지 않는다. Legacy fallback을 되살리면 다중 Gateway replica가 같은 occurrence를 중복 실행할 수 있으므로 허용하지 않으며, 지속적인 schedule 실행이 필요한 환경은 승인된 drain 검증 뒤 `claim` mode로 전환해야 한다.

`disabled`/`drain -> claim` activation과 `drain -> disabled` rollback preflight는 모두 nonterminal claim과 미검토 `execution_outcome_unknown`이 없는 durable ledger를 먼저 확인한다. Activation은 legacy/new schedule task, rollback은 new schedule task의 active/reserved/scheduled 상태와 Redis workflow priority queue depth도 확인한다. Broker queue payload는 파싱하거나 보관하지 않으며 DB/inspection/queue depth 확인 불가 또는 non-zero이면 fail-closed한다. 신규 disabled bootstrap과 disabled image-only rollout은 Worker 부재를 오류로 오인하지 않도록 transition preflight를 생략한다.

Gateway startup은 migration-managed table/enum을 `create_all()`로 생성하거나 보정하지 않는다. Demo/test bootstrap만 명시적으로 `create_all()`을 사용할 수 있다.

Gateway와 Worker는 공통 schema readiness service로 Alembic head, runtime 필수 column과 claim check/unique constraint를 검사한다. Online migration은 동일 DB connection의 bounded-wait PostgreSQL advisory lock과 production rollout 공통 concurrency group으로 직렬화한다. Helm/raw Kubernetes pod는 mode와 모든 dispatch 설정을 포함한 canonical fingerprint annotation을 Downward API로 process에 전달하며 `claim`/`drain`에서 fingerprint 누락 또는 실제 설정 불일치가 있으면 startup을 중단한다. 기존 Deployment에 annotation이 없는 최초 도입은 desired mode가 `disabled`일 때만 bootstrap으로 허용한다. 일반 독립 service rollout은 desired 값과 live Gateway/Worker fingerprint가 모두 일치할 때만 허용한다. 설정 변경은 승인된 이전 공통 fingerprint, 양쪽 desired/current, migration, 동일 commit Logger/Gateway/Worker image가 포함된 최종 manifest render, staged apply와 최종 검증을 한 coordinated workflow가 소유한다. Logger는 nullable system actor와 canonical schedule trigger 계약을 이해하는 동일 commit image로 먼저 배포하고, `claim` 활성화는 Worker 다음 Gateway 순서로 진행한다. `drain`/`disabled` 전환은 Gateway를 먼저 멈추며, active claim 상태의 설정 변경은 drain을 선행한다. 중간 실패 재실행은 선행 서비스가 동일 commit image/desired fingerprint이고 나머지가 승인된 이전 fingerprint인 경우만 재개한다.

Deployment spec의 image/fingerprint가 desired 값이라는 사실만으로 rollout 완료를 판정하지 않는다. Coordinated workflow는 commit tag가 이미 ECR에 있으면 기존 digest를 재사용하고, 없을 때만 build/push한 뒤 manifest를 immutable `repository@sha256`로 렌더링한다. `observedGeneration`, desired/updated/Ready/available replica 수, unavailable replica, 실제 non-terminating Pod의 spec image, container `imageID`, fingerprint와 Ready condition을 모두 검증한다. Logger도 같은 digest 수렴을 확인한 뒤 mode drain을 수행한다. 실패 후 재실행은 desired spec이 남아 있어도 Pod가 수렴하지 않았다면 해당 단계를 다시 수행한다. Dev 일반 배포도 live Gateway/Worker의 generation, replica와 실제 Pod Running/Ready/fingerprint가 수렴하지 않으면 disabled rollout을 시작하지 않는다. Activation/rollback drain은 Kubernetes의 전체 Ready Worker 집합과 Celery inspect 응답 집합이 일치해야 하며, queue와 active/reserved/scheduled 상태를 양방향으로 재관측한 연속 두 안정 구간이 모두 0일 때만 통과한다.

핵심 recovery는 expired dispatch/enqueue와 running deadline 격리를 먼저 처리한다. WorkflowRun visibility와 retention cleanup은 별도 UnitOfWork의 optional maintenance라서 실패가 claim/dispatch 진행을 막지 않는다. Outcome review는 acknowledgment 전용이며 rollback preflight와 redrive에서 분리한다.

`disabled`는 신규 claim/admission을 중지하지만 이미 생성된 operational ledger의 visibility와 retention 의무까지 해제하지 않는다. Schema가 준비된 환경에서는 visibility, terminal cleanup과 age signal을 계속 실행하며, schema가 준비되지 않은 최초 bootstrap에서는 maintenance를 시작하지 않는다.

검토되지 않은 `dead_lettered/execution_outcome_unknown`은 retention 기간이 지나도 cleanup하지 않는다. Exact claim outcome review가 기록된 뒤에만 dead-letter retention 대상이 되며, 일반 terminal row와 검토 완료 outcome unknown은 bounded retention으로 정리한다.

Schedule 전용 Celery task는 result backend에 workflow output, RAG evidence 또는 sync 상세를 저장하지 않는다. Publisher와 task 양쪽이 result 저장을 비활성화하고 task 반환값은 claim/status/finalization 같은 비민감 요약으로 제한한다. `rag.retrieve` 감사에는 canonical organization을 포함해 조직별 감사 조회에서 추적 가능해야 한다.

Schema rollback은 application rollback과 다르다. Schedule migration이 merge된 공통 Alembic graph에서 과거 공통 선조로 downgrade하면 sibling feature migration도 함께 제거될 수 있으므로 기본 schema downgrade는 지원하지 않는다. 파괴적 downgrade는 명시적 opt-in, 전체 백업과 영향 검토가 있는 복구 절차에서만 허용하며 일반 rollout 문서는 `claim -> drain -> disabled` application rollback만 안내한다.

운영 관측은 claim DB state를 source of truth로 유지하면서 created/conflict/enqueue/failure/duplicate/dead-letter/pending age/running age/missing run/outcome review의 low-cardinality structured signal을 best-effort로 남긴다. Signal에는 UUID, tenant/user id, idempotency key, raw input/output/exception을 label이나 message로 넣지 않는다. Scheduler와 Worker의 오류 로그도 static operation, bounded attempt와 exception type만 남기며 claim UUID와 raw exception message는 durable audit 대용으로 기록하지 않는다.

## Non-Goals

- 모든 workflow trigger에 공통 idempotency model을 도입하지 않는다.
- 외부 system side effect의 수학적 exactly-once를 보장하지 않는다.
- public claim 조회/redrive API나 schedule 운영 UI를 추가하지 않는다.
- Celery broker를 exactly-once queue로 교체하지 않는다.
- Claim을 장기 audit 원장으로 사용하지 않는다.

## Consequences

장점:

- Gateway replica 수와 duplicate Celery delivery에 관계없이 occurrence별 Workflow Engine admission을 한 번으로 제한한다.
- DB commit/publish 부분 실패를 durable state로 복구한다.
- system schedule이 사용자 권한을 위조하지 않는다.
- outcome unknown을 자동 replay하지 않고 운영 검토와 rollback gate를 연결한다.

비용:

- claim model, recovery/cleanup scanner, migration-first rollout 절차가 추가된다.
- admission 이후 외부 effect의 최종 결과가 불명확하면 자동 복구 대신 운영 검토가 필요하다.
- `WorkflowRun.user_id` nullable compatibility를 Gateway, Log System, Client에서 처리해야 한다.

## Follow-Up

- MBA-190에서 node adapter idempotency key 전달과 provider별 중복 제거를 구현한다.
- 비-schedule trigger 공통 execution admission/idempotency 모델은 별도 이슈와 ADR로 다룬다.
- 장시간 workflow에 독립 watchdog이나 실행 class별 deadline이 필요하면 Worker pool/resource 정책과 함께 후속 설계한다.
