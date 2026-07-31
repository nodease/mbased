# ADR-0053: Connection transaction과 lock 경계

Status: Accepted
Related ADRs: [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0048](ADR-0048-knowledge-collection-sync-execution-boundary.md), [ADR-0049](ADR-0049-connector-test-security-boundary.md), [ADR-0051](ADR-0051-connection-use-authorization-boundary.md)

## 배경

ADR-0051은 user-owned Connection을 현재 실행 주체 본인만 사용할 수 있도록 설정 저장 전과 외부 DB dial 직전에 같은 owner predicate로 검증한다. 그러나 권한 resolver가 caller의 SQLAlchemy session에서 ORM `Connection`을 반환하면 Gateway ingestion, Workflow Engine과 KC sync의 서로 다른 transaction 수명이 credential 해석과 외부 DB I/O에 결합될 수 있다.

Connection reference 저장과 삭제는 dangling reference를 막기 위해 같은 Connection row를 잠가야 한다. 이 lock은 DB 정합성에 필요하지만, lock wait가 무기한이거나 경로별로 KB/Document와 반대 순서로 획득되면 지연과 deadlock을 만든다.

## 결정

1. `ConnectionUseResolver`는 ID 정규화와 `Connection.id + Connection.user_id` authorization read만 담당한다. Runtime row lock, session 생성·종료와 외부 adapter 호출을 소유하지 않는다.
2. 외부 DB dial 직전에는 `ConnectionRuntimeSnapshotProvider`가 독립된 짧은 SQLAlchemy session을 연다. 같은 transaction에서 owner를 다시 검증하고 adapter type과 최소 credential configuration을 immutable in-memory snapshot으로 투영·복호화한 뒤 transaction과 session을 종료한다. Connector schema 관리 API는 current user scalar ID를 먼저 복사하고 기존 404/403 precheck transaction을 종료한 뒤 같은 provider로 dial-time owner를 재검증한다. Rollback 뒤 request-session ORM attribute를 다시 읽지 않는다.
3. `DbProcessor`는 ORM `Connection`, encrypted storage field와 복호화 규칙을 직접 해석하지 않는다. Snapshot provider가 성공적으로 종료된 뒤에만 connector를 생성하고 외부 DB를 호출한다. Snapshot은 DB에 저장하거나 API, metadata, audit, trace, exception에 projection하지 않는다. PostgreSQL database/username은 공백뿐인 값을 거부하되 저장된 identifier text를 임의 trim하지 않는다. 기존 SSH password-auth row에 encrypted SSH password가 없으면 agent/default-key compatibility 경로를 유지하고 `None`을 복호화하지 않는다.
4. Runtime snapshot은 dial 시작 시점의 권한 스냅샷이다. 이미 시작된 외부 작업을 owner 변경·삭제와 선형화하거나 즉시 취소하지 않는다. 즉시 revoke가 필요하면 lifecycle revision 또는 bounded lease를 별도 결정한다.
5. Connection reference 저장·교체·삭제처럼 dangling reference 정합성이 필요한 짧은 mutation만 owner Connection row를 `FOR UPDATE`로 잠근다. 전역 획득 순서는 `Connection -> KnowledgeBase -> Document/DocumentVersion`이며 역순 획득과 Connection lock을 보유한 외부 network/storage/provider 호출을 금지한다.
6. Connection reference row lock은 PostgreSQL local `lock_timeout=2s`를 적용한다. Lock 획득뿐 아니라 같은 reference UoW의 flush/commit에서 발생한 lock timeout, deadlock victim과 serialization failure도 rollback 뒤 새 session/transaction에서만 재시도 가능한 `connection.reference_busy` transient 오류다. 기타 저장소 장애는 `connection.reference_unavailable`, 삭제 대상의 live reference는 non-retryable `connection.in_use`, reference writer의 stale Document revision은 non-retryable `connection.reference_conflict`, hidden target은 `resource.hidden`으로 구분한다.
7. Gateway는 hidden을 기존 404, live reference conflict를 409, busy와 store unavailable을 raw detail 없는 503으로 mapping한다. Delete API의 기존 store-failure wire code `connection.delete_unavailable`은 compatibility alias로 유지하지만 domain code는 `connection.reference_unavailable`이다. Workflow Engine과 KC sync는 HTTP status를 사용하지 않고 busy/store unavailable을 `source.temporarily_unavailable`, hidden/configuration failure를 terminal safe reason으로 처리한다.
8. PostgreSQL runtime connector는 schema/fetch connection에 connect 5초, statement 5초와 read-only 기본 transaction을 적용한다. Fetch는 batch/total row cap과 총 16 MiB row payload cap을 적용하고 cap 초과를 일부 성공으로 반환하지 않는다. 종료·예외·취소 시 engine과 tunnel을 정리한다.
9. Lock 관측 정보는 outcome과 coarse wait/hold-duration bucket만 허용한다. Hold bucket은 실제 transaction 종료 시 기록한다. Connection ID/name, host, SQL, credential, raw driver/lock detail은 metric label, log, audit와 trace에 넣지 않는다.
10. 이 결정은 `connections` schema와 permission model을 변경하지 않는다. 신규 migration, organization-scoped 공유, Redis lock과 전체 Knowledge ingestion finalization 재설계는 포함하지 않는다.

## 검토한 대안

### Authorization resolver가 외부 I/O 종료까지 Connection row를 잠금

실행 중 owner 변경·삭제를 대기시킬 수 있지만 network latency가 PostgreSQL lock 수명이 되고 Gateway/Worker/KC의 다른 lock 순서와 결합되므로 채택하지 않았다.

### ORM Connection을 detach해 processor에 반환

Session 의존성은 줄지만 persistence shape와 encrypted credential 해석이 processor에 남고 immutable 최소 projection을 보장하지 못하므로 채택하지 않았다.

### Runtime credential snapshot을 Redis 또는 process cache에 보관

Revoke invalidation, secret retention과 새 source of truth를 만들기 때문에 채택하지 않았다.

### 모든 Connection mutation을 Redis queue로 직렬화

PostgreSQL row가 authoritative reference source이므로 별도 queue와 이중 장애 경계를 추가할 이유가 없다.

## 영향

- Gateway, Workflow Engine과 KC sync의 DB source runtime은 같은 snapshot provider를 사용한다.
- 외부 DB adapter가 실행될 때 snapshot authorization session은 닫힌 상태다.
- Reference save/delete 경합과 reference mutation commit의 transient PostgreSQL failure는 safe transient 결과와 전체 rollback으로 종료되며 동일 transaction의 부분 상태를 재사용하지 않는다. Reference writer는 Connection 다음 Document를 잠그고 최초 조회 revision을 다시 비교하므로 concurrent 설정 변경을 조용히 덮어쓰지 않는다.
- 기존 owner-only 권한, resource hiding, Connection schema와 즉시 revoke 비보장 계약은 유지된다.

## 잔여 위험과 후속 검토

- Snapshot 생성 직후 owner 변경·삭제가 발생해도 이미 시작된 외부 DB call은 계속될 수 있다. 즉시 revoke가 제품 요구가 되면 Connection lifecycle revision/lease가 필요하다.
- Python process memory의 decrypted secret zeroization은 보장하지 않는다. Snapshot 수명과 projection surface를 최소화하는 것으로 제한한다.
- KC sync의 document advisory lock과 version finalization transaction은 ADR-0048의 책임이며 이번 결정이 재설계하지 않는다.
