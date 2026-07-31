# ADR-0051: Connection 사용 권한 경계

Status: Accepted
Related ADRs: [ADR-0010](ADR-0010-resource-access-403-404-policy.md), [ADR-0022](ADR-0022-incremental-hexagonal-architecture-adoption.md), [ADR-0048](ADR-0048-knowledge-collection-sync-execution-boundary.md), [ADR-0049](ADR-0049-connector-test-security-boundary.md)

## 배경

현재 `connections`는 `organization_id`나 별도 `use` 권한 없이 `user_id` 소유 관계만 가진다. Knowledge DB source는 document에 저장된 `connection_id`로 외부 DB credential을 해석하지만, 기존 processor는 Connection UUID만 조회해 실행 주체와 소유자를 비교하지 않았다. 따라서 KB 또는 Collection 권한을 가진 사용자가 다른 사용자의 Connection UUID를 주입하면 그 credential을 간접 사용할 수 있었다.

ADR-0048은 KC sync의 임시 호환 정책으로 Connection 소유자가 해당 조직의 active member이면 worker가 사용할 수 있다고 정했다. 그러나 조직 membership은 현재 user-owned Connection의 사용 위임 근거가 아니며, 그 예외는 Gateway의 owner-only 정책과 충돌한다.

## 결정

1. 현재 Connection 사용 권한은 `connections.user_id == execution_subject_user_id`인 경우에만 성립한다. Workflow, Knowledge Base, Knowledge Collection 권한과 같은 조직 membership은 다른 사용자의 Connection 사용 권한을 부여하지 않는다.
2. Connection ID 정규화, 존재와 소유자 판정은 Shared `ConnectionUseResolver`가 한 query에서 수행한다. Gateway와 Workflow Engine caller는 owner 조건을 복제하지 않는다.
3. Knowledge DB source 설정 저장 전과 외부 DB dial 직전에 같은 resolver로 검증한다. Dial 직전 검증은 해당 외부 작업의 권한 스냅샷이며, queue 대기 중 삭제 또는 owner 변경은 adapter 호출 전에 fail-closed한다. Dial이 시작된 뒤의 회수를 현재 작업 중간에 선형화하거나 취소하는 계약은 포함하지 않는다.
4. Missing, malformed, deleted와 non-owner reference는 Knowledge use surface에서 모두 `resource.hidden`으로 처리한다. Connector 관리 detail/schema API의 기존 403/404 계약은 변경하지 않는다.
5. Document metadata에는 opaque `connection_id`만 Connection reference로 저장한다. Connection name/type/host/port/database/username, encrypted/decrypted credential과 SSH 상세를 document metadata, processor result, chunk label, audit 또는 log에 복제하지 않는다.
6. Credential 복호화 실패는 암호문 fallback 없이 configuration failure로 닫는다. Resolver 저장소 장애는 외부 adapter를 호출하지 않고 safe temporary failure로 처리한다.
7. ADR-0048의 “Connection owner가 조직 active member이면 KC worker가 사용할 수 있다”는 임시 문장은 이 결정으로 대체한다. KC sync requester가 Connection owner가 아니면 worker는 해당 target을 safe configuration failure로 종료한다.
8. `connections` organization scope, 공유 `use/manage` 권한, lifecycle state와 migration은 이번 결정에 포함하지 않는다. 향후 도입 시 resolver 내부 정책과 데이터 모델을 새 ADR로 교체한다.
9. Connection reference 저장·삭제 직렬화를 위한 MBA-273의 owner row lock은 기존 lifecycle 경계에 유지한다. 반면 runtime use의 lock mode, 획득 순서, transaction 수명, timeout과 회수 선형화는 MBA-302에서 별도 ADR로 결정하며 Shared authorization resolver에 포함하지 않는다.

## 검토한 대안

### KB 또는 Collection 권한으로 Connection 사용 허용

Content 권한을 credential 사용 권한으로 확대하고 현재 데이터 모델에 없는 위임을 암묵적으로 만들기 때문에 채택하지 않았다.

### Connection 소유자가 같은 조직의 active member이면 허용

실행 주체에게 부여되지 않은 credential을 조직 membership만으로 사용할 수 있어 user-owned 모델과 충돌하므로 ADR-0048의 임시 예외를 유지하지 않는다.

### Organization-scoped Connection RBAC를 함께 도입

Lifecycle, migration, manage/use 권한, UI와 회수 계약이 필요한 별도 기능이므로 P1 권한 우회 수정에 포함하지 않는다.

### 설정 저장 시점에만 검증

Queue 대기 중 삭제·소유권 변경을 반영하지 못하므로 사용 시점 재검증과 함께 사용한다.

### 외부 DB I/O 전체 동안 Connection row 잠금

실행 도중 삭제·owner 변경을 대기시킬 수 있지만, 장시간 외부 I/O를 포함하는 transaction 수명과 서비스 간 lock order를 함께 정의하지 않으면 지연과 deadlock 위험을 만든다. P1 권한 우회 수정의 범위를 넘어가므로 채택하지 않고 MBA-302의 별도 아키텍처 결정으로 분리한다.

## 영향

- 다른 사용자가 소유한 Connection UUID는 Knowledge upload, process, preview, workflow pre-execution sync와 KC sync에서 사용할 수 없다.
- 기존에 다른 active organization member의 Connection을 참조하던 KC target은 owner가 직접 실행하지 않으면 configuration failure가 된다.
- 외부 DB dial 시작 시점까지 소유권 변경·삭제를 다시 반영하지만, 이미 시작된 외부 작업을 중간 취소하지는 않는다.
- DB schema와 migration은 변경하지 않는다.

## 잔여 위험과 후속 검토

- Runtime use의 transaction/lock protocol, revoke linearization, timeout과 관측성은 MBA-302에서 별도 ADR로 결정한다. PostgreSQL을 authoritative lock manager로 사용하며 애플리케이션 전역 대기열을 이 ADR에서 도입하지 않는다.
- 공유 Connection이 필요하면 organization scope, explicit `use/manage`, revoke linearization과 background execution subject를 먼저 정의한다.
- 현재 `connections`에는 lifecycle state가 없으므로 삭제와 owner 변경 외의 disabled/revoked 상태는 표현할 수 없다.
